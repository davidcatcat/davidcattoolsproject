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
  76 tests, PASS, no ResourceWarning.
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


## New implementation handoff and runner support

David supplied the A11b.2.2.1 prompt after an independent conversation merged PR #46.
GitHub confirms main `585caf7d4c9dad20afd3beb38e5140d6dadf89e0` and PR #44/#46 merged.
The new task authorizes only supervisor/fake-child containment, not real ADD or 2.2.2.
The outer helper now supports a new branch/Draft PR as well as existing-PR review.
The task/README/WORKFLOW document the prompt-to-Git-to-Mac-script handoff.

Additional synthetic helper checks cover branch/PR collisions, unpublished commits,
read-only new-task precheck, initial Codex implementation, new Draft publication,
required full/Mac gates even in related mode, required-probe failure STOP without
retry/publication, plus strict Mac evidence schema/environment/hash/count/latency/
identity/absence validation. Mac evidence itself is mocked in pipeline fixtures;
none of those fixtures is a real Mac containment result.

The runner appends final command/probe evidence to the developer's validation report
and re-reviews the full final diff. Source/test hashes control expensive gate reuse;
report-only updates cannot establish different runtime behavior. Mandatory Mac failure
or unsupported capability stops for assessment. Independent security review remains
pending and is not substituted by ordinary Claude PASS or exact-head CI.

Actual new-task Codex auth, trusted -I installation, supervisor implementation,
Mac signals/kqueue/kernel identity and x20+x20 parent-SIGKILL probes are NOT TESTED
by this authoring environment. David must run the published task on the real Mac.
No application code, actual application checks, alternate checkout or live model call
was performed while authoring this helper update.

Final checks also reject a publication hook that changes already-tested bytes before push.


## Squash merge preflight repair — 2026-10-07

David's first new-task launch stopped before implementation because the helper used
ancestry alone. GitHub confirms PR #46 was squash-merged: main 585caf7 has one parent
18365bb, while PR HEAD 91aebb has parent 687f3f9. Both commits have exact tree
9a0021efb52576771a851dc68d293c16945ab7fe. This remote evidence does not claim a
new local Mac inspection.

The new guard accepts only normal ancestry or exact merged PR HEAD/branch/repository
plus identical trees and merged-commit ancestry in pinned main. Seven additional
fixtures cover normal ancestry, valid squash handoff, unpublished extra commits,
unmerged/wrong-repo/branch/base PRs, missing merge ancestry, differing trees and
Git ancestry errors. 57 standalone fixtures and Bash/Python syntax checks PASS.
No project code/checks or live model calls were run. New-task Mac execution remains
pending; the stopped preflight left files preserved and had not created its branch.

## Report-only NEEDS_DECISION checkpoint publication — 2026-10-07

David's real Mac run `20261007T064116Z-68730` invoked Codex successfully and stopped at F3 NEEDS_DECISION with only the allowlisted report added. No candidate source/tests, containment probes or canonical suites ran. The coordinator imported his uploaded terminal transcript into `reports/ai-loop/checkpoints/A11b.2.2.1-containment-20261007-20261007T064116Z-68730.md`; this is a normalized transcription, not the original Mac bytes. Observations and design reasoning remain unverified claims, not PASS.

The runner now supports opt-in `publish_decision_checkpoint` for an initial report-only new-task NEEDS_DECISION. It publishes one immutable curated checkpoint to the pinned control branch via GitHub API, checks both scope and report bytes again before ref update, rejects control drift/path collision/truncated tree, uses force=false, and verifies the remote blob. It does not modify the local feature branch/index or upload raw logs. API failures preserve the primary decision. All other stop types remain local. No approval/resume or alternative F3 design is introduced.

69 standalone fixture tests PASS, including initial-decision publication and STOP without tests/review/PR, API failure preserving NEEDS_DECISION, opt-in/result/mode/repair guards, report-only/base guards, symlink and size/content rejection, ref drift/collision/race, report changes, private log exclusion, strict task setting and remote hash verification failure. Syntax/compilation and Bash launcher syntax PASS. This is helper authoring evidence; automatic publication by gh on David's Mac has NOT RUN. Actual containment, full suites, independent helper/security review and safe task recovery remain pending.

## Repo-local launcher install-only path — 2026-10-07


David selected `scripts/ai-loop.sh` inside the canonical Mac repository. The
launcher is already versioned on the control branch; its bytes are unchanged.
The runner now supports `--install-launcher`: the existing parent bootstrap
fetches the control revision and this mode installs only the pinned launcher.
It validates the Git source and Bash syntax, bounds the source to 64 KiB,
rejects symlink/non-directory paths and different existing contents, accepts an
identical file without rewriting, and exclusively creates a missing launcher.
It retains the runner lock but returns before task loading, provider/dependency
checks, branch preparation, tests/probes or feature publication. It cannot be
combined with task/check/test-mode options. No task recovery is added.

76 standalone authoring fixture tests PASS, including exact source installation,
unrelated/stopped report preservation, idempotent install, different-file and
symlink preservation, invalid Git source/Bash syntax rejection, installer main
flow bypass of models/tests/task checks, and incompatible option rejection.
Python compilation and unchanged Bash launcher syntax PASS. README and WORKFLOW
now use the repo-local command and distinguish install-only from actual execution.

No remote Mac executor is connected. Installation on David's Mac is NOT RUN here;
he must execute the one install-only command. F3 design resolution, task recovery,
actual containment gates and independent review remain pending.
