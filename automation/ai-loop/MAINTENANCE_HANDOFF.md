# P0 maintenance handoff — Issue #52 and ai-loop self-recovery

> **Current resolution (2026-10-09): Issue #52 CLOSED.** The one-use `--maintenance-issue52` bootstrap successfully preserved and published the candidate as `ab1f396b00ba11704f0b370a70bf1a31f85f2ee9`; Mac 210 tests PASS, read-only Claude PASS (operator log), and [exact-head GitHub CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37826584204) Python 3.9/3.14 210+15 PASS each. Operator reports local clean main and C1 not started. Issue #53 remains OPEN for **general reusable** maintenance/resume, not for this one-use repair. The historical incident narrative below describes pre-fix evidence and is retained for audit. C1 is approved for normal precheck using `.git/ai-loop/run.sh` but may only start if that precheck passes. No manual prompt relay or auto-merge.

Status: **BLOCKED / NOT IMPLEMENTED / NOT AN AUTHORIZATION TO RUN C1** (2026-10-09).
Owner: coordinator; Codex CLI is the source/test writer, Claude CLI the read-only independent reviewer. The Mac operator is **not** a prompt courier.

## Why this handoff exists

The existing [task.md](task.md) and [task.json](task.json) describe C1 Issue #51, **not** Issue #52. The current `scripts/ai_loop.py` only supports `implement_new_pr` and `review_existing_pr`; the existing `--resume` contracts are specific prior-run recovery types, not generic adoption of arbitrary dirty code. `--check` correctly STOPs on a tracked `scripts/test_ai_loop.py` modification because C1 does not allow that path. Repeating C1's launcher or manually relaying prompts to Codex/Claude is not a repair.

Issue #52 is a test-fixture separation regression in the temporary ai-loop helper: six CI errors (Python 3.9 and 3.14, 207 tests) because historical 76644 recovery tests read today's live mutable `automation/ai-loop/task.json`. Archived immutable fixture:
`automation/ai-loop/history/20261008T100821Z-76644.task.json`.
GitHub CI run: https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37815598386

Mac operator reported a **local uncommitted Codex patch** only to `scripts/test_ai_loop.py`, with 66 additions and 10 deletions, adding 3 tests. `python3 scripts/test_ai_loop.py` initially produced 16 failures and 78 errors under the default macOS `/var/folders` temporary-root symlink. Using a resolved physical temp root `TMPDIR=/private/var/tmp` yielded **210 tests / OK / exit 0** in the operator's Mac log. This is local 3.13 evidence only; **no Claude PASS, no Python 3.9/3.14 GitHub green, and no published patch** have been established. The coordinator cannot see the private local diff from GitHub; do not invent it.

## Immediate repair target — bounded P0, *not* C1

1. Preserve the canonical Mac checkout and its **uncommitted** `scripts/test_ai_loop.py`; do not reset, clean, stash, overwrite, silently switch branches, or start C1. Preserve previously parked stash data from the older f3 branch.
2. Work only on `scripts/test_ai_loop.py` and the narrow **separately reviewed maintenance/bootstrap route** necessary to continue this specific self-repair. Keep `scripts/ai_loop.py` product safety checks, C1 contracts, archived fixture bytes, and the application unchanged except for explicitly reviewed, separately scoped runner-maintenance implementation.
3. Validate the existing local diff before any new Codex call. Tests must derive historical recovery fixtures from the archived 76644 contract while validating current C1 independently. Retain all negative security assertions (wrong path, unexpected scenario, duplicate recovery, digest/evidence drift, process proof, authorization/false PASS).
4. Use a **physical macOS temporary root** for helper fixture tests, without removing the runner's path/symlink escape checks. Confirm the location is real and owned/writable before using it. Treat the prior `/var` symlink fixture error as an environment/test-path issue, not permission to bypass security checks.
5. Run the full helper suite (currently 210 cases) on the exact candidate. Arrange an independent Claude **read-only** review of the full diff and relevant immutable fixture/loader, with structured findings and exact candidate identity. If FAIL, bounded Codex repair and re-test automatically; never have the operator copy a prompt between agents.
6. Only after local tests + independent reviewer PASS, publish exactly the authorized repair to existing PR #45 (`automation/ai-loop`), subject to exact Git head/branch and changed-file checks; require helper CI **Python 3.9 and 3.14** success on the published head. Keep all results honest. Do not auto-merge.
7. Keep Issue #51 / C1 **NOT STARTED** until these gates pass. On actual completion, resume C1 using the existing single launcher entrypoint and pinned C1 task contract.

## Required permanent behavior: maintenance handoff, not user prompt relay

The temporary helper must support a **separate, explicit, bounded self-maintenance/resume contract**, stored in Git as markdown plus machine-readable metadata; **do not hijack `automation/ai-loop/task.json` (live C1)** or re-purpose an old `--resume` run id. The maintenance route must:

- Recognize exactly one approved dirty tracked path and verify frozen base/head/allowed-path inventory and file digests. Before accepting it, snapshot candidate bytes **outside the tracked tree**, reject symlinks, directory escapes, unrelated/staged mutations, and untrusted source changes. Never assume arbitrary dirty work is safe.
- Select an existing PR and branch by pinned GitHub metadata; protect the single-writer and exclusive lock. No branch switching, clobbering or automatic stashing of other user work.
- Consume a frozen Markdown prompt/contract from Git to invoke Codex **only if needed**, run deterministic tests, hand cumulative diff/results to Claude read-only, allow bounded repairs, and publish with a lease / exact-head check. Default to spending **zero** model tokens on reproducible deterministic diagnostics; bound diff/context and retry budgets.
- Report a concise, machine-readable `STOP` with category, last successful phase, preserved evidence location, exact blocked dependency and next machine-actionable recovery route. A normal interrupted run should be resumable under validated provenance rather than requiring manual copy/paste of fresh prompts.
- Preserve the existing canonical-repo-only policy, mandatory safety assertions, independent review, exact-head CI and no auto-merge. Do not expand a temporary bootstrap into a competing permanent agent platform: equivalent native workflow ultimately belongs in llmtool A11b/A11c.
- Require new negative tests: unrelated dirty work; staged changes; changed candidate after snap; missing archived fixture; wrong C1/live task; spoofed path; stale remote head; interrupted model/CI; duplicate resume; reviewer failure; Claude tool/mutation attempt; leaked prompt/source; evidence mismatch; false PASS; Mac symlink temp path; multi-writer collision.
- Make ongoing operation: coordinator updates the **Git task contracts**, operator invokes the **single standard launcher**, all intermediate Codex/Claude prompts/tests/repair/CI are orchestrated. User decisions occur only on material authorization/design gates; no multi-line manual Mac commands for routine repair.

## Acceptance claims and known gaps

**Confirmed:** Mac helper tests reported 210/210 with an explicit physical TMPDIR; GitHub PR #45 remained draft at `ac03992b6025a2f1fbea786be4b9823ecbef325f` when the handoff was authored.

**Not confirmed:** private local patch correctness, remote helper CI, Claude security/code review, automated self-maintenance runner, C1 execution. This is a coordination/implementation handoff only; it does not itself execute Mac programs or change the active task authorization.

Do not claim this document alone repairs Issue #52 or implements maintenance-mode automation.
