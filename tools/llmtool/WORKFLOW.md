# llmtool development workflow — Codex Cloud and local Mac

## Execution environment

Use Codex Cloud when selected by the user for repository investigation, development,
documentation, review, offline tests, builds and CI. Use the actual Mac for verification
that depends on its VPN, credentials, files, operating system or running services.
Cloud is a separate computer; it does not inherit the Mac's sessions or network.

| Work / verification | Environment | Evidence |
|---|---|---|
| Source/docs changes, review, unit tests, fake-runtime/mock integration, lint/build | Configured Cloud checkout; local Mac also supported | Exact SHA, commands, exits, results |
| GitHub branch, push, PR, CI | Cloud when access is available | Remote branch SHA, PR URL, CI run SHA/status |
| Company LLM `10.29.7.58:4000`, model `gw`, VPN coexistence | Actual Mac with company VPN | Route/reachability, auth if applicable, exact live marker |
| Existing Claude CLI / Codex CLI authentication, Keychain-dependent setup | Actual Mac | Executable/version, direct marker and llmtool E2E result separately |
| Mac localhost, Docker/VM/database, running processes, unpushed files, macOS isolation | Actual Mac | Actual dependency and revision tested |
| Public provider live tests | Explicitly configured and authorized environment | Real inference evidence; Cloud availability alone proves nothing |

Cloud network, tools, Git push and provider credentials depend on configuration and
must be checked, never presumed. Credentials in Cloud do not prove Mac authentication.
Do not copy Mac auth files/Keychain material into Cloud, expose an internal endpoint,
or change VPN/routes to bypass a missing prerequisite. Fake-runtime tests remain useful
Cloud evidence but are never called real provider validation.

## 1. Establish repository and scope

Read AGENTS.md, README, ROADMAP, applicable skills and current review reports.
Local Mac root remains `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
In Cloud discover the task-configured checkout; do not create a clone/worktree to
repair a missing repository. Verify the repository is `davidcatcat/davidcattoolsproject`.

Run from the discovered root:

```sh
pwd
git rev-parse --show-toplevel
git remote -v
git branch --show-current
git status --short --branch
git rev-parse HEAD
git fetch origin
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main
```

Keep credentials out of reported remote URLs. If fetch fails, report it and do not
claim tracking refs are current. Preserve unrelated changes. Work on a task feature
branch based on the confirmed intended base; do not automatically update main.
The Cloud exception does not permit other Mac paths or agent-created project copies.

## 2. Plan verification before implementation

List acceptance checks as Cloud-capable, Mac-required, or not required for this change.
A check is Mac-required because of an actual dependency, not merely because it is a
smoke/integration test. Declare required merge gates before execution; do not demote a
failed/missing required check after seeing its result. Documentation-only changes use
documentation validation and need no live provider request or application build.

## 3. Complete Cloud work

Implement the agreed scope, update tests/docs, run relevant available checks, review
the final diff and repair issues within the task. Record skipped checks and why.
Use implement-feature, review-change and safe-git. Authorized branch publication
may proceed even while required Mac checks remain pending; that is not merge readiness.
If push/PR access is unavailable, retain the patch/commit and give exact handoff steps.
Do not stop independent Cloud work merely because one Mac-only check cannot run.

## 4. Publish a Mac verification handoff

Record the following in a dated validation report under `reports/` and link it in the PR:

- Repository, feature branch, base SHA and exact implementation SHA.
- Cloud environment/platform/tool versions and commands with exit codes.
- Each Cloud check result, CI URL/run SHA, and final diff review result.
- Each pending Mac check, its prerequisite, copyable commands, expected marker/exit,
  and reason it is required (or explicitly optional).
- Separate code/documentation completion, required verification, review and merge status.

Use PASS, FAIL, SKIPPED-CONFIG, SKIPPED-AUTH, SKIPPED-ENVIRONMENT or NOT-TESTED per
[validate-runtime](../../skills/validate-runtime/SKILL.md). For pending required
checks, state **LOCAL VERIFICATION REQUIRED / MERGE BLOCKED**; this is report
bookkeeping, not a new llmtool task state or implemented command.
A screenshot/model assertion without tested revision is insufficient evidence.

## 5. Verify on the actual Mac

Start at the canonical Mac repository, inspect branch/status and preserve unrelated work.
Fetch the published feature branch using safe-git; establish the intended tested SHA
without blindly pulling, resetting, stashing or overwriting changes. If existing work
prevents switching safely, report the blocker. Do not use `/private/tmp` worktrees.

Confirm the expected implementation is present and the installed llmtool/runtime
belongs to that checkout. Run only applicable pending checks using validate-runtime:
small marker-only requests, explicit config, bounded timeouts and no secret dumps.
Company LLM checks require the actual company VPN; check required internal/external
and GitHub connectivity separately. Mac-only services/auth remain Mac evidence.

Append platform, runtime version, exact tested SHA, commands/exits/markers and results
to the report. Skipped checks are not PASS. A failed required test blocks merge;
an environment/auth failure is not automatically a code regression.

## 6. Review and merge gate

Merge only when task authorization, required Cloud checks/CI, external review and
applicable required Mac verification are satisfied. Do not mark a milestone DONE
merely because Cloud implementation or mocks passed. Optional tests can remain
NOT-TESTED when the milestone explicitly permits that, with the limitation reported.

Evidence binds to the tested code/config/test revision. Any relevant change after
Mac validation invalidates affected results and requires rerunning those checks.
Report-only commits may cite the tested implementation SHA when the reviewed diff
proves runtime/config/tests unchanged. Fetch the latest target and review integration
changes; rerun affected checks when integration alters the validated behavior.
Never reuse historical PASS as a new live result or merge automatically because CI is green.

This is a development process, not an A11 executor or A12 remote frontend feature.
It does not add commands, credentials, execution capabilities, dependencies, automatic
approval or fallback, and does not change milestone numbering or implementation status.

## References

- [Codex Cloud overview](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [Repository guidance](../../AGENTS.md)
- [Roadmap](ROADMAP.md)
