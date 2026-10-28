# A11c PM continuation and Agent Skills audit — 2026-10-22

- **Brief:** Owner, 2026-10-22: tidy up the A11c Agent Skills and the PM workflow. Scope is limited to consolidating existing rules and making small repairs. No widening of Git, provider-spend or security authority.
- **Author:** Claude Developer, assigned by the Owner. **Formal review:** the independent Codex Reviewer Agent, as the Owner chose for this task. The author does not self-approve or merge.
- **Base:** `origin/main` `8b5b1eab65c868d1a33ce67b5c4af3c289cd3e6e`. D6 task `a11c-pm-continuation-20261022`.
- **Normative outputs:**
  - [PM Continuation Contract](../skills/ai-team-delivery/pm-continuation.md): the single rule.
  - [Worker availability table](../tools/llmtool/ROADMAP.md#worker-availability--live-status): live, dated state.
  - [four-layer company-LLM availability rule](../skills/company-llm-delegation/SKILL.md#availability-classification).
  - [A11c work packages and runtime gaps](../docs/history/llmtool-roadmap-archive-20261028.md#a11c-work-packages-and-continuation-runtime-gaps).

This report is evidence. It is not a rule: the linked files are normative.

## 1. Why the PM kept stopping

1. **No rule required a next decision.** The guidance said what a Reviewer FAIL or a BLOCKED Task means for that Task. It never said that the PM must then choose and record a next step, or check the queue. So a PM report could end at "BLOCKED" or "waiting" with no next owner.
2. **Live Worker state was written into role rules.** "Until Claude quota recovers (expected 2026-10-25)" appeared in four places. "OpenCode is a normal Developer route" stood with no live status. The PM could not tell a permanent role from today's quota. On 2026-10-22 OpenCode hit HTTP 429 while Claude was already back, and neither fact was reflected in the rules.
3. **Company LLM availability was decided from a single indirect signal.** On 2026-10-21 the company route was recorded as UNAVAILABLE because `scutil --nc list` showed no VPN service ([#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6302396061)). On 2026-10-22 it was recorded as `UNAVAILABLE_VPN_OFF`. TCP to the gateway was in fact reachable (§6, S4).
4. **The runtime cannot continue on its own past the PM callback.** After `SEND_TO_REVIEW`, nothing dispatches a Reviewer. After a Reviewer rejection, nothing calls the PM or the Developer. `task complete` is a manual command. Each of those points therefore waited for a human prompt (§5).
5. **Escalation conditions disagreed.** AGENTS.md lists three Owner questions. Owner outcome reporting §6 lists more material decisions. With no rule saying which one governs, a PM could treat ambiguity as "ask the Owner".

## 2. Contradictions

| # | Where | Contradiction | Disposition |
| --- | --- | --- | --- |
| C1 | CLAUDE.md, AGENTS.md, delivery SKILL, WORKFLOW, ROADMAP product goal | "Codex Reviewer until Claude quota recovers (expected 2026-10-25)", while the Owner on 2026-10-22 says Claude is the fixed formal Reviewer and available. Today the Owner also assigned the Codex Reviewer to this PR. | **Fixed:** the role rule is now "Claude is the fixed formal Reviewer; the Codex Reviewer substitutes only when the Owner assigns it or the dated table marks Claude unavailable". The date was removed from all five places. **Reviewer/Owner: please confirm this wording (§7).** |
| C2 | AGENTS.md (three escalation categories) vs [Owner outcome reporting §6](../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md#6-decision-authority-and-escalation-boundary) (spend, egress, credentials, GO/NO-GO, product/priority change) | Two lists with no rule for which governs. Also noted in the [2026-10-21 meeting §6.5](../docs/meetings/2026-10-21-llmtool-product-requirements.md). | **Fixed minimally:** owner-escalation.md now says that an action beyond existing authority is a STOP for that action and gets a §6 decision brief, and that only the dependent action waits. No new escalation category was added. |
| C3 | `company-llm-delegation` describes a triage-only, "does not implement A11c routing" scope, while AGENTS.md defines company `gw` as a Developer route | The skill gate never mentioned the Developer route. | **Partly fixed:** the availability rule now covers both uses. The triage-only body was left unchanged; it is out of scope. |
| C4 | WORKFLOW ("remind the Owner to connect VPN") vs PM-owned routine decisions | A reminder is not a decision. But "VPN off" was being used as the availability verdict. | **Fixed:** the VPN list is now only a hint for that reminder; availability follows the four layers. |

## 3. Stale states

| Location | Stale content | Disposition |
| --- | --- | --- |
| ROADMAP current-milestone A11c row | No mention of the WP1/WP2/WP4a merges (#256–#263) | **Fixed:** link added to the new WP and gap table |
| ROADMAP, WORKFLOW, AGENTS | No Worker live state at all (OpenCode 429; Claude available; AGY paused) | **Fixed:** dated table added |
| #120 2026-10-22 records | Company `UNAVAILABLE_VPN_OFF` | **Superseded in the table:** TCP PASS; API, inference and task layers UNKNOWN |
| ROADMAP A11c.1a | "each existing Codex Developer and Claude Developer session" (2026-10-14 pilot) | Historical record; left unchanged |

## 4. Duplicated rules

- **The dated Reviewer substitution** appeared in five files. It now lives once, in AGENTS.md; the other files point to it.
- **"A GitHub comment is not a wake"** appears in AGENTS.md, the delivery SKILL (twice), governance and WORKFLOW. These copies were left unchanged; the contract links AGENTS.md instead of adding another copy. They could be consolidated later.
- **Developer routing** is described in AGENTS.md, the delivery SKILL, WORKFLOW and ROADMAP (twice). This was not changed, to keep the diff small, and is noted for a later docs pass.
- **The VPN reminder** appears in both WORKFLOW and ROADMAP A11c.1a. Left unchanged.

## 5. Auto-handoff capabilities not yet implemented

The ROADMAP gap table maps each gap to its owning existing package. No new supervisor is proposed.

| Gap | Missing capability | Owner |
| --- | --- | --- |
| R1 | Reviewer dispatch after the PM returns `SEND_TO_REVIEW`. There is no production ReviewAuthority factory and no Claude llmtool profile, and the Codex Reviewer has no verifiable session receipt. | WP2/WP3 |
| R2 | On a Reviewer rejection (`FORMAL_REVIEW_NOT_ACCEPTED`): a callback to the original PM and a re-dispatch to the original Developer | WP3 |
| R3 | An automatic Reviewer-PASS trigger (`task complete` is manual), and a verifiable Reviewer native session (it is UNKNOWN today) | WP2/WP3 |
| R4 | Publishing the direct-D6 Developer → original-PM callback (`e6e8e763`: no PR, no exact-head CI) | WP2 |
| R5 | A persisted per-Worker availability observation that the PM can read. It must be a record, not a router. | WP3 |

## 6. Acceptance matrix

All commands ran in the task D6 worktree with the task-local venv (Python 3.13.3, `requirements-ci.txt` pins).

| # | Scenario | Result | Evidence |
| --- | --- | --- | --- |
| S1 | Developer PASS → Reviewer | **PARTIAL / Runtime gap R1** | `test_executor_event_returns_bound_result_to_original_pm_before_review`: the original PM receives the bound result once and `SEND_TO_REVIEW` is persisted. A Mac live run on the unpublished WP2 candidate also returned `SEND_TO_REVIEW` ([#120](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6336524046)). **No Reviewer is dispatched**; the PM does that step manually. |
| S2 | Reviewer FAIL → Developer repair | **Skills fixed; Runtime gap R2** | `test_github_review_requires_d6_actor_session_and_live_exact_head`: a later `CHANGES_REQUESTED` defeats the APPROVE and becomes `FORMAL_REVIEW_NOT_ACCEPTED`. `test_completion_missing_gate_waits_for_reviewer_without_calling_pm`: the Task stays `WAITING_REVIEW` with no PM call. There is no false acceptance, but also no automatic return to the Developer. |
| S3 | OpenCode paused → another authorized Worker | **Skills fixed; not a runtime routing feature (by design); R5 open** | llmtool does not choose Workers ([ROADMAP A11c.1](../docs/history/llmtool-roadmap-archive-20261028.md#main-ai-decisions-and-llmtool-execution-boundary-issue-120-owner-direction)). Observed: OpenCode returned 429 and the Owner reassigned WP-OC-02 to the Claude Developer; that PR, #270, merged. The contract makes this a PM duty. Runtime support for reading availability is R5. |
| S4 | Company LLM: TCP reachable but API fails → classified correctly | **PASS (runtime code path, loopback)** | New test `test_availability_layers_are_classified_separately` uses the real `task submit`, TaskStore and openai-compatible client over a loopback HTTP server. Results: closed port → `NETWORK_PROVIDER_UNREACHABLE` (0 server calls); TCP+HTTP reached but 401 → `AUTH_PROVIDER_REJECTED`; 200 with an invalid body → `MODEL_RESPONSE_INVALID`; valid → `PM_RESPONDED`. Nothing secret is persisted. **Live company gateway:** `nc -z -G 3` to the configured endpoint at 2026-10-22T12:46:27Z succeeded (exit 0). API auth, inference and task layers are **UNKNOWN**: this task had no authorization for a gateway call, and none was made. |
| S5 | One Task BLOCKED → other independent Tasks still run | **PASS (runtime code path)** | New test `test_blocked_task_does_not_block_an_independent_task`: Task A ends `PM_UNAVAILABLE/AUTH_PROVIDER_REJECTED`; independent Task B in the same TaskStore reaches `PM_RESPONDED`; A's record is byte-for-byte unchanged. |
| S6 | Reviewer PASS → Integrator | **PARTIAL / Runtime gap R3** | `test_original_pm_profile_receives_evidence_once_and_recommendation_persists`: with accepted exact-head review and CI evidence, the original PM is called once and `ACCEPT` is persisted; no replay. In this test the evidence collectors are mocked; GitHub review and CI validation have separate tests (`test_task_completion.py`). The trigger is the manual `task complete`. |
| S7 | Work package complete → original PM continues automatically | **PARTIAL / Runtime gap R4** | On main the Host/Worker path is automatic (`test_successful_host_event_invokes_only_the_bound_task_callback`), with claim-once and no replay of UNKNOWN outcomes (`test_ambiguous_original_pm_result_is_unknown_and_never_replayed`). The direct-D6 path exists only in the unpublished `e6e8e763`. **No live automatic continuation was observed in this task. None is claimed.** |

**Test runs:**

| Command | Result |
| --- | --- |
| `.venv/bin/python -B -m unittest discover -s tests -p test_pm_continuation_acceptance.py` | 2 tests, OK |
| `-p test_task_completion.py` | 31 tests, OK |
| `-p test_task_submission_cli.py` | 33 tests, OK |
| `-p test_opencode_foreground_host.py` | 23 tests, OK |
| `make check PYTHON=.venv/bin/python` | 1,879 tests, OK, 2 skipped, exit 0. The first attempt had 21 failures, all `No module named 'llmtool'` in child processes, because the new venv lacked the editable install. After `pip install --no-deps -e .` into the task venv, imports were verified to resolve from this worktree, and the rerun passed. |

## 7. Items for the Reviewer and the Owner

1. **The CLAUDE.md / AGENTS.md Reviewer wording (C1; audit-time concern).** The audit draft allowed Codex substitution whenever the availability table marked Claude unavailable, without an end date. Follow-up repair narrows this to the Owner's current dated authorization through Claude quota recovery, expected 2026-10-25; it does not establish an indefinite Codex substitution route.
2. **Process disclosure.** Before the worktree was created, a `git fetch origin` ran in the canonical checkout. It only updated remote-tracking refs. Under the safe-git rules this should have been left to the Integrator.
3. **Next executable A11c work (audit snapshot).** This is the PM's decision; nothing had been started when the report was prepared. Subsequent status is noted where known below.
   - WP2: publish `e6e8e763`, in its existing D6 task, by its existing Codex Developer.
   - Then WP3 R2 and R1/R3, in sequence in the same task line (Codex Developer).
   - At the audit snapshot, #273 ENV-1 awaited the Codex Reviewer. It subsequently merged via PR #273 at `2e6c5dc`; ENV-0, ENV-2 and ENV-3 remain open under Issue #267.
   - The #266 and #272 REVISE items are docs fixes (Codex Developer, or a Claude Developer only on Owner assignment).
   - OpenCode stays paused until a bounded free call succeeds.
   - Company `gw` may take a low-risk test task only after the PM records the data basis and an authorized llmtool call establishes layers 2–3.
