# llmtool Coordinator handoff — 2026-10-15 12:29 Asia/Taipei

> **Evidence cutoff:** GitHub remote main `6306f2dc9b9b2d618f30374333b9b170a2854996` and Issue #120 entries through #6185084241 (2026-10-15 05:23Z). Recheck fresh GitHub at takeover. This document is a **ChatGPT conversation handoff**, not a product gate, permission grant, or claim of local Mac observation.
>
> **Branch discipline:** Document added on isolated docs branch only; do not merge automatically or touch either Developer Worktree, local canonical refs, active source, CI or settings. Existing #149 is a prior handoff draft and may be stale; this report updates the conversation's handoff in a separate PR.

## 1. Objective and operating protocol

The Owner is building `llmtool` to orchestrate Codex CLI, Claude CLI and eventually a company OpenAI-compatible LLM, reducing human prompt-courier work and costly model tokens. ChatGPT is the **Coordinator**; CLI Developers execute source tasks in their pinned Mac sessions/worktrees; independent reviewers sign off. The human should not need to repeatedly copy small instructions between CLIs.

**Each user message “review repo” requires:**
1. Read fresh GitHub `main`, Issue #120 newest comments, open PRs and exact-head CI. Previous status is historical, never overwrite newer evidence with it.
2. Report **what changed since last review**, relevant security/function/CI gate, real blockers vs LATER, and next objective in concise Traditional Chinese. Keep source/fake distinct from live capability; don't misreport fake/CI PASS as live.
3. Proactively give **the exact NEXT_AI** (role, original session, fixed root) and **one complete paste-ready prompt**, including durable evidence URLs, scoped steps, acceptance gates, how to report back. When genuinely parallel independent handoffs are useful, explain carefully but minimize the Owner's manual work.
4. Never tell the Owner to wait for an AI doing hypothetical background work. GitHub comments do not awaken local CLI agents. They need explicit invocation until a separately reviewed dispatch system is approved.
5. Developer owns implementation, runs its **own advisory reviewer agent** and bounded repairs in the same session. One formal **non-author** contract review for major scope and one formal **non-author** source review premerge. Do not repeat accepted contracts, C3, historical CI, benchmarks, or ping-pong low severity issues. `NOW` functional/security correctness bugs fixed before release; `LATER` tracked, not release blockers.
6. Preserve Owner's chosen `danger-full-access` and `approval_policy=never`; AI should not change them or claim authority to veto Owner preference. Nonetheless distinct live egress, new runtime routes and branch mutation gates remain explicitly tracked.
7. Git shared refs belong to all worktrees: fixed roots, no cross-root edits; bounded `CLAIM`/matching `RELEASE` around actual shared-ref mutation; preserve old branch/stashes/venvs/baselines/ignored contents. Source write only after independent **new C3** acceptance when contracted; no unauthorized live/model/queue/RPC.
8. Company LLM needs VPN for real endpoint access, but **VPN ≠ live/egress authorization**. Inform Owner when approved live testing actually requires connection.
9. Keep Issue #153 **HOLD / CLOSED**. Owner explicitly said “#153 先不要, 我們先做可行性分析, 我想一下”. No implementation, source contract, reviewer loop, polling script, launchd, daemon, scheduling or auto-dispatch for #153 without a NEW explicit Owner decision. This stop supersedes #6184731098; evidence #6184950676 and #6184950092.

## 2. Original CLI identities (must be verified by each real CLI)

| Role | Original session | Fixed Mac root | Responsibility |
| --- | --- | --- | --- |
| Codex Integrator | `01a12e4b-1ac5-7c11-9071-6c01c3aafb5f` | `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` | coordination, independent new C3, final pins, Claude-authored source review/merge |
| Codex Developer | `01a12f8c-6a46-71c2-a7b3-3e6211ec2e9c` | `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex` | Native CR-K2-B four-file development after new C3 |
| Claude Independent Reviewer | `081de22e-d514-4288-9d1f-48f488979271` | `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` | nonauthor Codex contract/source security+functional review |
| Claude Developer | `5e05868e-9b7c-41f3-b29b-de3ed351aef4` | `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude` | finished C1B source; finished company Phase 0 readiness DESIGN_ONLY, no new source authorization |

## 3. Product and PR status

- `main` SHA **`6306f2dc9b9b2d618f30374333b9b170a2854996`** at cutoff.
- [PR #151 Native Composition A1](https://github.com/davidcatcat/davidcattoolsproject/pull/151) **MERGED**, source/fake only, accepted independent Claude review; postmerge CI Python 3.9/3.14 SUCCESS.
- [PR #152 C1B Test-Triage Worker](https://github.com/davidcatcat/davidcattoolsproject/pull/152) **MERGED**; first independent review found a real Unicode surrogate SEV-2 bug, Developer fixed it with red-first test, independent delta APPROVE; postmerge CI Python 3.9/3.14 SUCCESS. C1B is **offline/fake only** with public live route blocked, one call/zero retry/fallback and advisory output.
- [PR #149](https://github.com/davidcatcat/davidcattoolsproject/pull/149) **open Draft**, older documentation-only handoff; do not assume it reflects newest facts, don't merge just because another docs report exists.
- No evidence of actual Native Pickup / true agent-to-agent automatic routing, company live integration or cost/quality ROI; those remain NOT_VERIFIED / BLOCKED. `ai-loop` remains fallback, not primary engine.

## 4. Active CR-K2-B HOST_ROLLOUT Native work — **highest NEXT step**

- Formal four-file [contract #6184402548](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6184402548); independent Claude reviewer Security+Functional ACCEPT, NOW0 [#6184573766](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6184573766).
- Integrator Final Pins, OLD_FINAL independent acceptance and authorized turnover [#6184679109](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6184679109). Pin R1 **actual upstream writer** spellings: `event_msg.payload.type` = `task_started`, `task_complete`, `turn_aborted` (not read aliases `turn_started/turn_complete`); absence of `error` means success while `error:null` invalid; only exact `interrupted`, `final_answer`, and PascalCase `UserMessage/AgentMessage` items under stipulated constraints. All other bounds, file identity/ordinal/partial-line/fail-closed rules remain.
- **NEW development since previous ChatGPT turn:** original Codex Developer actually completed the authorized turnover [#6185034965](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6185034965); matching CLAIM #6185018773. Fixed worktree branch `feat/codex-issue120-cr-k2b-host-rollout-v1-20261015`, exact HEAD `6306f2dc9b9b2d618f30374333b9b170a2854996`, tree `fac752b825b2c041e0ddfd16d0e2856731e7ffcb`, clean. Fresh Task-Start Candidate `/private/tmp/issue120-cr-k2b-task-start-qvumkuij/task-start-candidate.json`, SHA256 `2b0eab2f1313ada3cad9f18ddbd9326966ba45923641f991e0bea06bec56a664`, inventory index/tracked/untracked/ignored = 538/538/0/1917, author pinned sanity PASS. **That sanity is NOT independent acceptance.**
- **As of cutoff, NEW_C3 = PENDING; SOURCE_WRITE = HOLD.** Next AI should be **original Codex Integrator** to independently verify matching RELEASE, branch/tree/candidate hash, entire inventory/retention/stash/refs and execute pinned new C3. If PASS, publish `NEW_C3_BASELINE_ACCEPTED / SOURCE_WRITE_AUTHORIZED` under Issue #120, assign original Codex Developer source/fake. Do not mistakenly assign Codex Developer to redo the completed turnover.
- Four-file source scope only: `tools/llmtool/llmtool/codex_pickup_native.py`, `tools/llmtool/tests/test_codex_pickup_native.py`, `tools/llmtool/README.md`, `tools/llmtool/WORKFLOW.md`. No transport, policy, ledger, provider or extra controller without evidenced contract amendment.
- Scope: bounded read-only synthetic HOST_ROLLOUT JSONL reader/codec/coverage with correct real-writer format, pinned physical identity, contiguous Paginated ordinals, exact session/client/item/turn association, strict resource bounds, sticky fail-closed on missing/unknown/contradictory evidence. This is **not** authorization to read a real host rollout, perform Step-0 census, queue, resume, RPC, execute model, Native Gate B or unattended dispatch.
- After C3, same Codex Developer executes whole source/fake package, advisory agent, related regressions, Draft PR + exact-head CI; original Claude Independent Reviewer performs one formal nonauthor Security+Functional source review, then authorized integrator convergence/merge.

## 5. Company LLM Phase 0 parallel lane — review only, no live authorization

- Claude Developer published [Phase 0 Readiness Review #6185084241](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6185084241), **READ_ONLY/DESIGN_ONLY**, based on merged C1B. No source edit, model call, VPN, live grant or new branch.
- Missing: (1) **grant-bound live seam** (public C1B currently blocks it); (2) preregistered real-format synthetic capture set and gold labels; (3) Owner's explicit route/data/cost/time/VPN grant. Exact HTTP wire-size/digest consistency should be handled in a bounded source package; success usage triplet is available; company token accounting/ROI currently UNKNOWN.
- Proposal only: `P0-LIVE-SEAM-V1` separately scoped/reviewed source, then **one preregistered Company-only Phase 0 ≤7 requests** using controlled synthetic real-format captures, no reruns except agreed incomplete continuation, transparent fidelity/quality/privacy/usage evidence. **Do not automatically authorize or implement** from this recommendation. When real network testing approved, tell Owner to connect company VPN. Historic company endpoint `http://10.29.7.58:4000/v1/chat/completions` model `gw` was verified historically, not now; never assume API key or present live endpoint as currently available.
- Native #120 and company Phase 0 lanes are logically independent; however the current authoritative priority is Native CR-K2-B new C3, and Claude Developer now has a finished readiness report, not an assigned source task.

## 6. Explicit user preferences and roadmap boundary

- Owner wants **parallel development when file scopes don't overlap** and fewer manual relay messages; Developer + own advisory Review Agent, then **one** independent reviewer at a real release gate; bug severity: fix NOW correctness/security problems immediately, defer unrelated LOW/LATER, consolidate findings.
- Owner repeatedly asks “review repo”; Coordinator must supply NEXT_AI prompt **proactively**, not merely restate status.
- Milestone shorthand: A–D foundation and Native Composition **source/fake milestone complete**; E true Native Pickup pending rollout reader + later Step-0/Gate B; F automatic pickup/develop/review/handoff not E2E verified; G Company Worker merged but live testing/ROI pending; H llmtool self-development unverified. “4/8” refers only to broad milestone count, **not 50% engineering work**.
- `#153` experiment for polling/Git inbox is **HOLD/CLOSED** by Owner, feasibility discussion only. Do not launch an automation or workstream under this issue.
- The handoff itself must not become a second scheduler, source contract, reviewer opinion, or merge grant.

## 7. Template for future Coordinator responses

```
【llmtool review repo — timestamp Asia/Taipei】
main SHA: ...
new facts since previous check: ...
PRs / CI / blockers: ...
Native vs company live gate: ...
NEXT_AI: <one original role + session + fixed root>
Why now: ...
Paste-ready prompt:
- Evidence URLs/exact branch and candidate identifiers
- Task / scope / acceptance
- Gate restrictions and one evidence handoff
- No repeat of already accepted checks
```

## 8. NEXT_AI as of this handoff

**Original Codex Integrator** `01a12e4b-1ac5-7c11-9071-6c01c3aafb5f`, canonical root, to independently accept new CR-K2-B C3 based on [#6185034965](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6185034965). On success, **then** original Codex Developer `01a12f8c-6a46-71c2-a7b3-3e6211ec2e9c` implements four-file package. Recheck GitHub first in new conversation; these gates may already have moved.

**Source links:** [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120); [#153 Owner HOLD](https://github.com/davidcatcat/davidcattoolsproject/issues/153#issuecomment-6184950092); [#151](https://github.com/davidcatcat/davidcattoolsproject/pull/151); [#152](https://github.com/davidcatcat/davidcattoolsproject/pull/152); [Company readiness](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6185084241).
