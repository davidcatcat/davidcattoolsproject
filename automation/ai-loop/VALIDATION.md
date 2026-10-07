# AI loop helper validation — 2026-10-07

## Updated task and provenance

The first helper contract targeted new A11b.2.2.0 implementation, but PR #46 was
subsequently published. David authorized replacing that stale contract with
**independent review / bounded repair of existing PR #46**. The current contract
pins base `18365bb079835f3b413dd135ea742213bb1a7724` and initial PR HEAD
`687f3f93d6d46b6d4fb5a95a7cfd1fb17170f53c` on
`feat/llmtool-a11b2-2-0-cleanup-20261007`.

Authoring uses connected GitHub APIs on the known automation/ai-loop branch. No
project clone, worktree, alternate project checkout, application test execution,
Mac runtime or live model execution is claimed. Newly authored helper files were
checked independently in the Work scratch environment; temporary files in fixture
tests are synthetic runtime fixtures, not project checkouts.

Changes are limited to the helper/contract/instructions on the control branch.
They do not modify PR #46's implementation, main, llmtool product behavior or
A11b.2.2.1/.2/.3. The control branch is fetchable before PR #45 is merged.

## Passed authoring checks

- `bash -n ai-loop-delivery/scripts/ai-loop.sh`: exit 0.
- `python3 -m py_compile ai-loop-delivery/scripts/ai_loop.py`: exit 0.
- `python3 -W error::ResourceWarning -m unittest discover -s ai-loop-delivery/scripts -p test_ai_loop.py -q`:
  36 tests, PASS, no ResourceWarning.
- Task JSON parses and names the actual PR branch/head/report. Tests use
  tools/llmtool/.venv, root-discovery PYTHONPATH and the Mac system Python.

Original fixtures cover CLI completion versus review PASS, malformed/missing
results, quota/turn limits, stale reviewed SHA, hidden High/blocking findings,
optional Low findings, missing evidence, scope expansion, unrelated untracked
work, agent Git writes, stdin/logging, timeout, no signaling reaped PGIDs, and
exact-head CI success/failure/missing/old results.

New fixtures cover open/pinned PR identity, closed/merged/moved PR rejection,
wrong repository/branch, reuse and fast-forward of an existing branch, read-only
precheck, stale remote HEAD and local divergence. Synthetic full-run fixtures
also exercise:
- Existing tests/review PASS: no Codex call, no duplicate PR, source unchanged,
  report published on the existing branch, original PR edited and completion saved.
- Blocking finding: one bounded Codex repair, tests and re-review, same-PR publication.
- Two repairs exhausted: STOP, no publication/PR edit, preserved work/logs.
- Related mode: related local tests only, including after repairs.
- Full mode: reuse unchanged initial full PASS; repair with related checks and
  run final full checks on changed bytes; final full failure returns to bounded
  repair.
- Test phases select the configured commands, retain per-command results and
  stop at the first failure instead of spending time on remaining commands.

David requested selectable local related/full modes on 2026-10-07. The task's
default is related, with full canonical checks still mandatory in final exact-head
CI. Full mode retains both Mac interpreter checks. Reports distinguish local
full NOT RUN from a real local full PASS and from GitHub CI evidence.

## Observed earlier run and pending new-mode evidence

David's terminal output for the previously published runner shows both local
canonical commands, Claude review, then waiting for CI at publication HEAD
`91aebb12c25c63c1998caa85ad9fc5e15a4e993d`. GitHub confirms PR #46 at that SHA
and an in-progress pull-request llmtool CI run when inspected. This is evidence
of the earlier run reaching publication; it does not validate the new mode code
or establish Codex authentication (the successful path skips Codex).

- Bootstrap fetch/task download and safe branch handoff on the user's canonical repo.
- Actual Codex/Claude flag compatibility and saved login/quota.
- Project targeted/canonical checks using both specified actual Mac interpreters.
- Live Claude → optional Codex repair → tests → Claude re-review.
- Same-branch Git publication, exact-head remote CI and existing PR body update.

Actual new-mode checks remain pending, not PASS. --check is an optional low-cost preflight; a normal
launch performs the same preflight before model work. The helper itself has author
self-review and offline fixtures only; independent helper review remains pending.
Future ordinary Claude implementation reviews do not substitute for separate
security/QA/containment gates. No automatic merge, next slice or Work callback is
implemented.
