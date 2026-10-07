# AI loop helper validation — 2026-10-07

## Provenance and scope

Authoring used connected GitHub APIs pinned to main
`18365bb079835f3b413dd135ea742213bb1a7724`, the merged PR #44 commit.
No local project checkout, clone, worktree, production llmtool mutation, Mac
verification, or live model execution is claimed. The user authorized publishing
a development helper and the next task to Git. Standalone newly authored helper
files were syntax/fixture-checked in the Work scratch environment; repository
application tests were not run from an alternate project checkout.

This adds the independent launcher, Python runner, fixture checks, instructions,
and a bounded A11b.2.2.0 task. It does not change llmtool product code or enable
A11b.2.2.1/.2/.3. No files on main are changed by control-branch publication.

## Passed checks

- `bash -n ai-loop-delivery/scripts/ai-loop.sh`: exit 0.
- `python3 -m py_compile ai-loop-delivery/scripts/ai_loop.py`: exit 0.
- `python3 -W error::ResourceWarning -m unittest discover -s ai-loop-delivery/scripts -p test_ai_loop.py -q`:
  20 tests, PASS, no ResourceWarning.
- Task JSON parsed; explicit source/report allowlist, immutable base, feature-only
  branch, deterministic targeted/canonical commands, and at most two repairs.

Fixtures verify CLI completion is distinct from review PASS, missing/malformed
results, quota/turn-limit output, wrong reviewed SHA, hidden High/blocking findings,
optional Low findings, missing evidence, scope expansion, unrelated untracked work,
agent Git writes, stdin handoff, local stdout/stderr logs, timeout handling, no
signaling reaped PGIDs, exact-head CI success/failure, and missing/old CI checks.

## Pending actual Mac evidence

- Bootstrap fetch/task download against the user's canonical repository.
- Actual installed Codex/Claude flag compatibility and saved authentication.
- Existing canonical .venv and project targeted/canonical test execution.
- Real Codex → Claude → repair loop, publication, CI wait and PR creation.
- Network/VPN availability on the actual Mac where applicable.

These are pending, not PASS. A real invocation may encounter auth, quota, sandbox,
branch or dependency failures and must stop with preserved evidence. The helper
has no unattended approval-resume protocol or automatic callback into this Work
thread. It requires the Mac to stay connected and awake. This Work thread currently
has no local Mac executor or installed/authenticated CLI pair; typing `run` here
does not yet launch the Mac workflow.

## Review boundary

The helper received author self-review and fixture checks only. Independent
review of the helper itself remains pending. Ordinary Claude reviews of future
task implementations do not substitute for formal security/QA gates. The
launcher never merges main and never starts the next task automatically.
