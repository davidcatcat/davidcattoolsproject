# CURRENT ACTIVE TASK — Independent Security Review + Functional QA for Draft PR #55 (Issue #56)

## Authority / identity

- This is **NOT** a fresh C1 development task and **NOT** another Codex implementation.
- Exactly pinned review target: PR #55 at `67d02be6bc8cb3f237c5060edda3321f6d885bf3`, base main `8e29024656f873431f6ad83e45015b57bfe381a9`.
- Branch: `feat/llmtool-c1-test-excerpts-20261009`, existing local canonical Mac checkout only.
- Prior completed C1 implementation task is archived at `automation/ai-loop/history/20261009-C1-issue51-completed-task.md/json`; **never re-use its `initial_decision_recovery`**.
- Two separate independent role reviews, in distinct read-only Claude CLI sessions, pinned to the *same immutable* PR HEAD: `security` then `functional_qa`. No Codex writer call. Runner-controlled tests and review, never user prompt relaying.
- Mac operator: one standard script entrance `bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh` **only after coordinator verifies current exact-head helper CI**. If the precheck STOPs, no models start; preserve all work.
- Publish a **sanitized** role-status/count/path-only audit summary to GitHub Issue #56; keep the model's untrusted raw output and detailed findings private in `.ai-loop/<run>/`. This is a model-assisted specialist gate, not an external human certification or merge authorization.

## Frozen allowed source inventory

Exactly 5 C1 source/tests/docs files plus the one runner-owned accepted report (6 total) as `allowed_files` in adjacent task.json. No tracked or untracked source edit; no commit, push, force, branch recreation or PR HEAD mutation.

Expected functionality: `llmtool test-excerpts --file PATH --exit-code INT --json`. One explicitly selected read-only capture, no subprocess/provider/network/Git mutation, no secret exfiltration, no summary invented as truth. Return bounded raw evidence with SHA, original byte offsets and preserved captured failure status. Do not enable company-model routing or token-saving claims.

## Independent Security Review

Read full cumulative diff, actual implementation, CLI dispatch and negative tests. Independently examine no-follow directory file access, hardlink/symlink/TOCTOU, fd cleanup, size/offset bounds, controlled UTF-8+ANSI+bidi, rejected credentials, safe output/error categories, false-success prevention and no implicit config/network/providers/shell/Git. Record actual findings with file and concrete evidence. `PASS` only if no blocking security defect; otherwise `FAIL`/`NEEDS_DECISION`. Do not call any test outcome PASS merely because parser successfully executed.

## Independent Functional QA

Read actual implementation and exercise/inspect focused regressions plus full Mac canonical tests. Verify real stdlib unittest failure bodies and pytest short-summary, repeated and parametrized IDs, zero/nonzero/negative captured exits, mixed failures, source SHA/byte ranges, cutoff/omission, incomplete unknown reports, malformed/unsupported data and source unchanged. JUnit XML not supported in this narrow C1; original broad MVP includes JUnit-style fixtures, so explicitly record *broader-plan acceptance incomplete* and do not silently claim all C1a delivered. Assess known Low usability gaps: false-positive sensitive regex (`basic test` and `token =`), and non-BrokenPipe stdout OSError returns 1 rather than captured 7. Identify material new blockers only from reproducible evidence, not preferences.

## Gates and success criteria

- Runner checks exact PR HEAD, base, identity, six-file diff, immutable source bytes and clean Git index/status pre/post model runs.
- Deterministic related syntax/test modules, plus **both full Mac canonical suites** must exit zero; any failure STOP and no pass. GitHub CI for PR HEAD [#37836667084](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37836667084) independently confirmed 3.9 and 3.14 success; 1289 tests, 1 skip each.
- Claude sessions are *read-only*, independent, separate prompts and outputs, maximum 2400 seconds each. No command execution, no staging or source mutation.
- A blocked first specialist gate stops rather than spend second model tokens; never turn review error-envelope into PASS.
- GitHub Issue #56 receives only immutable revision, test/status flags, role verdict counts and allowlisted file names. The full model text stays local; do not upload secrets or raw captures. Final coordinator/human acceptance, merge, C1b company-only Phase 0 and C1c measured token savings are **NOT** authorized.
- If any STOP, preserve candidate and private evidence; create a separate scoped repair/review contract before another script run; never make the user the prompt courier.

No script or AI model is allowed to automatically merge Draft PR #55 or advance C2.
