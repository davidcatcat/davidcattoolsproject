# llmtool QG-QA1 required bugfix validation — 2026-10-05

## Task Contract v1 (pinned before production changes)

- ID: QG-QA1-REQUIRED-BUGFIX-20261005; version 1.
- Repository: davidcatcat/davidcattoolsproject; canonical Mac root only:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Fetched base: `fe169aff967f4e7329128cf434c93ff3a3002b30`.
- Branch: `fix/llmtool-qg-qa1-required-bugs-20261005`, created from origin/main.
- Scope: Q-BUG-1 domain/context-correct strict-parser and explicit-profile errors;
  F1 durable interrupted finalization only for handled CLI TERM/HUP cancellation;
  D-34b thin product-entrypoint Ctrl-C boundary including CLI import/parser.
- Forbidden: D-34a interpreter startup, optional legacy broken pipe, DG-2/DG-3,
  HTTP TERM/HUP changes, generic signal/UX/security redesign, A11b/A11c, retry/fallback,
  new execution/mutation capabilities, weakened strict parsing, unrelated artifacts,
  alternate repository/clone/worktree, reset/stash/force/history rewrite/merge.
- Acceptance: each required finding reproduced with failing permanent tests before
  its production fix. Feedback wording remains feedback; orchestration/benchmark
  wording matches domain; explicit missing profiles distinguish saved active errors.
  Both fake CLI providers finalize TERM/HUP with durable interrupted summary,
  complete trace/healthy doctor, original 143/129 and tree/runtime cleanup; max_runs=1
  accepts the immediately next save. Off/failing persistence never changes signal exit;
  INT remains 130/interrupted. Only lifecycle-origin cancellation is finalized.
  Thin startup boundary returns 130 with fixed stderr for import/delegation/parser
  interruption; argparse SystemExit/usage 2 and TERM/HUP remain untouched.
- Evidence: public synthetic reproducers; deterministic tests and real subprocess
  signal/readiness handshakes; targeted CO-14/D-F1/D-31/D-34b/D-40–50 re-QA and key
  new-user/ask/diagnostics + failure/manual-retry journeys; focused then full canonical
  checks on actual checkout Python, 3.9 and available 3.14; exact-head GitHub 3.9/3.14
  offline checks/clean-tree SUCCESS. Historical/live evidence is not current evidence.
- Delivery: logically separate fixes and report commit, normal push, new Draft PR;
  final exact HEAD/CI recorded in publication evidence per WORKFLOW. Stop for Claude
  targeted re-QA; no merge or A11b. Preserve historical reports and nine baseline
  untracked benchmark/reasoning/case paths. Coordinator is the sole Git writer.

Status: local required fixes and targeted checks PASS. Publication/exact-head CI
is pending at this report cutoff; Claude targeted re-QA and merge adjudication
remain pending. This report does not declare the full QG-QA1 gate passed.


## Publication and evidence identity

- Actual environment: canonical Mac checkout, not Cloud, clone or worktree.
- Base / freshly fetched origin/main: `fe169aff967f4e7329128cf434c93ff3a3002b30`.
- Branch: `fix/llmtool-qg-qa1-required-bugs-20261005`.
- Implementation SHA: `805caaab5ccedeb4b9eae8b801bf24eadee271d9`.
- Q-BUG-1 commit: `e77947af34f83ca1acce4a73dd245e783a98ed68`.
- F1 commit: `6ad4b29c33647c414b06b068804dfeee4bc45c84`.
- D-34b / contract documentation commit: `805caaab5ccedeb4b9eae8b801bf24eadee271d9`.
- Task Contract v1 SHA-256 (body between its heading and Status):
  `d14ad4a575c60deb871f2d90e4724f4c5ba67375ca6892be5766192802288896`.
- Tested 17-file manifest SHA-256:
  `e6fedc62d920825c4121a87e9a505286980fe5578f2886a138e0bf7c24f0a7d0`.
  Algorithm: sorted repo-relative UTF-8 path, NUL, exact file bytes, NUL,
  concatenated for every implementation/test/README file listed below.
  Each working file and the implementation commit blob was checked against the
  individual tested digest after commits. No implementation byte changed afterward.
- The report commit is documentation-only. Per WORKFLOW, its final HEAD, Draft PR
  and exact-head CI identity belong to the PR description/Checks and final handoff;
  this report does not create recursive commits to embed its own future SHA.
- GitHub CI at report cutoff: NOT YET RUN for the final report head. The publisher
  must check Python 3.9/3.14, Offline canonical checks and Working tree is clean
  on that exact head. Older DG-1/QA CI is not substituted for new evidence.
- Coordinator performed development, regression tests, ordinary diff review and
  Git publication. No independent reviewer model/cost claim is made. Claude is
  the pending independent targeted re-QA reviewer; no auto-merge is authorized.

## Exact changed files

Production/package/launcher files (11):

- `tools/llmtool/llmtool/feedback.py`
- `tools/llmtool/llmtool/benchmark.py`
- `tools/llmtool/llmtool/benchmark_cli.py`
- `tools/llmtool/llmtool/orchestration.py`
- `tools/llmtool/llmtool/config.py`
- `tools/llmtool/llmtool/providers/cli_lifecycle.py`
- `tools/llmtool/llmtool/cli.py`
- `tools/llmtool/llmtool/entrypoint.py` (new)
- `tools/llmtool/llmtool/__main__.py`
- `tools/llmtool/llmtool.py`
- `tools/llmtool/pyproject.toml`

Tests (5 files; 37 new test methods):

- `tools/llmtool/tests/test_qg_qa1_error_context.py` (new, 15)
- `tools/llmtool/tests/test_qg_qa1_signal_persistence.py` (new, 11)
- `tools/llmtool/tests/test_entrypoint_interrupt.py` (new, 7)
- `tools/llmtool/tests/test_qg_qa1_journeys.py` (new, 4)
- `tools/llmtool/tests/test_cli_provider_lifecycle.py` (existing fixture extension;
  original assertions retained, no duplicate TestCase discovery)

Documentation (2): `tools/llmtool/README.md` and this new report.
Historical formal QA, security, DG-1 validation/review/re-review reports and
ROADMAP are unchanged. No packaged skill or CI workflow changes.

## Reproduction before each production fix

All commands ran at the canonical root using checkout Python 3.13.3. The new
permanent tests existed and failed before the corresponding production fix.
Reproducers use public CLI entry calls or actual subprocesses with synthetic
profiles and fake providers. No real Claude/Codex/API/token/VPN was used.
Prefix for focused commands (append modules listed in the tables):

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v
```

| Finding | Test module / before log | Expected | Actual before fix |
|---|---|---|---|
| Q-BUG-1 | `test_qg_qa1_error_context`; `llmtool-qa1-context-before.log` | Domain-correct orchestration/benchmark/requested-profile errors; original feedback and genuine active-provider errors | 15 tests, 17 failing assertions including subcases, exit 1; malformed plans/specs and schemas said feedback; explicit missing profiles said active provider |
| F1 | `test_qg_qa1_signal_persistence`; `llmtool-qa1-f1-before-final.log` | CLI TERM/HUP exits 143/129 with durable interrupted summary and safe secondary warning | 11 tests, 6 failures, exit 1; four TERM/HUP cases had cleaned provider tree/cwd but no summary; two unsafe-store cases omitted warning. SIGINT/off/arbitrary-SystemExit controls passed |
| D-34b | `test_entrypoint_interrupt`; `llmtool-qa1-startup-before-final.log` | Fixed stderr, no traceback/host path, exit 130 after product startup boundary | 7 methods, 13 failing assertions, exit 1; four deterministic boundary failures and nine real startup-handshake samples with traceback and rc -2 |

F1 startup readiness captures fake provider PID, child and grandchild plus the
actual temporary cwd. After TERM/HUP on the unfixed code all owned running
processes were gone and cwd removed, while `summary.json` was absent. The defect
is diagnostics finalization, not a regression of DG-1 process containment.

Test-harness iteration is kept distinct from product evidence: an early F1 trial
used an incorrect doctor wording assertion and imported the fixture TestCase
for duplicate discovery; both were corrected before the authoritative baseline
and before the production fix. An early startup trial let KeyboardInterrupt
abort unittest; the assertion helper was corrected before the authoritative
baseline. Those harness errors are not counted as confirmed bugs.

## Q-BUG-1 implementation and regression coverage

The same A7 strict parsing helpers accept fixed caller-owned context labels.
There are no duplicate parser implementations or input-derived error labels.
Benchmark validators bind benchmark context; spec/suite/case and nested typed
regression parsing identify their actual domain. Orchestration supplies plan,
step or result labels. Feedback defaults and its own wording remain feedback.
Duplicate keys, nesting, UTF-8/control/size/list/schema semantics are unchanged.

Explicit CLI/environment provider selection and explicit orchestration/retrieval
profile binding now distinguish a missing requested profile from an invalid
saved active provider. Profile values/raw JSON/host paths are not echoed.
Existing config/input error precedence and provider validation remain covered.

Permanent public cases include:

- `test_malformed_feedback_retains_feedback_context`
- `test_malformed_orchestration_plan`, `test_orchestration_plan_extra_field`,
  `test_orchestration_plan_too_many_steps`
- `test_malformed_benchmark_spec`, `test_malformed_benchmark_suite_manifest`,
  `test_benchmark_schema_error`, `test_malformed_benchmark_regression_case`,
  `test_nested_benchmark_regression_schema_error`
- `test_strict_duplicate_depth_controls_keep_domain`
- `test_explicit_ask_missing_provider_profile`,
  `test_orchestration_explicit_missing_profile`,
  `test_retrieval_explicit_missing_planner_and_synthesizer`
- `test_genuinely_invalid_active_provider_retains_active_wording`,
  `test_valid_feedback_benchmark_and_orchestration_offline`

Each invalid-profile/malformed-input case asserts zero provider factory calls,
no store creation, no traceback/raw input/path. Existing happy paths still pass.

## F1 implementation, signal and persistence invariants

A tiny `CLIHostTermination(SystemExit)` marker replaces the lifecycle's existing
TERM/HUP SystemExit only after owned process-tree/runtime cleanup. `_run_ask`
catches only this marker with code 129/143, observes finish(interrupted), emits
safe secondary diagnostics if applicable, then re-raises the original exit.
Arbitrary SystemExit is not reclassified. No global signal manager or HTTP
provider handler was added. Observer failures stay secondary.

The process-group ownership/kill-before-reap/idempotence, handler restoration,
inherited SIG_IGN and scoped SIGCHLD policies from PR #29 remain unchanged.
Signal handlers still do no kill/wait/I/O/cleanup/exception raising. Repeated
signals after handler restoration may use normal OS disposition, as already
explicitly documented; a later forceful signal can interrupt finalization.

| Fake provider | TERM | HUP | INT | Persisted interrupted record / immediate next save |
|---|---|---|---|---|
| claude-cli | 143, tree gone, cwd removed | 129, tree gone, cwd removed | 130, tree gone, cwd removed | PASS for all three |
| codex-cli | 143, tree gone, cwd removed | 129, tree gone, cwd removed | 130, tree gone, cwd removed | PASS for all three |

`test_{claude,codex}_{term,hup}_finalizes_and_immediate_next_save` and both INT
contract tests require summary status interrupted, final run.end event with
matching run_id, no reservation/partial trace, public runs not incomplete,
trace state=complete, and doctor rc0 with
`store=present run_count=1 active_count=0 unknown_count=0`.
Doctor's existing conservative structural contract is not expanded into repair
or complete content-health validation. A second explicit successful fake ask
with max_runs=1 immediately saves, without altering time or one-hour grace.
Each invocation launches exactly one fake provider; no retry/fallback.

Two persistence-off tests confirm absent LLMTOOL_HOME stays absent. Two unsafe
store tests confirm safe DIAGNOSTICS_STORE_UNSAFE warnings with original TERM/HUP
exits and untouched unsafe directory contents. The unrelated SystemExit test
confirms no interrupted finalization. The HTTP control test uses an actual
localhost request and TERM: default rc -15, no traceback, incomplete persisted
run remains. HTTP HUP was not separately probed here; its source path is unchanged.

## D-34a / D-34b boundary and startup tests

Console packaging now points at `llmtool.entrypoint:main`; module and direct
launchers use the same very thin lazy-import guard. It catches KeyboardInterrupt
only, returns 130 and fixed `llmtool: interrupted` stderr. `cli.main` includes
parser construction/parse_args in its existing interrupt boundary. Usage
SystemExit 2 and handled CLI TERM/HUP 129/143 are never swallowed.

Deterministic unit cases cover lazy import, delegated call, parser construction
and parse_args. Nine real subprocess samples (three per CLI-import, parser-build
and parse-args phase) use test-only import/argument hooks to announce readiness
then receive an actual SIGINT. All return 130 with exactly the safe message;
no traceback or host path. No fixed-sleep race is the sole signal detector, and
no production sleep/environment hook was added. Module/direct normal help,
version, invalid usage and providers smoke also pass (0/0/2/0).

D-34a remains a platform limitation: Python/site initialization and the time
before entry into the product guard are not handled by llmtool. This round does
not claim full protection from arbitrary interpreter-startup timing. No earlier
interpreter Fatal Python error was recategorized as a product regression.

The actual checkout console was reinstalled with
`tools/llmtool/.venv/bin/python -m pip install --no-deps -e tools/llmtool`
(exit 0), then installed entrypoint metadata and the installed script were
confirmed to target the new thin entrypoint. Help/version/usage/providers with
an isolated synthetic HOME returned 0/0/2/0 and made no user-config/state writes.
An initial no-build-isolation attempt lacked setuptools.build_meta in the venv;
standard build isolation resolved packaging with no runtime dependency change.

## Targeted re-QA and focused results

These are new execution results, not reused Claude/historical evidence. The
full historical 271-case QA was not rerun. The affected scenarios were mapped
to permanent public tests instead:

| QA scope | Current coverage / result |
|---|---|
| CO-14 | malformed plan/schema/step bounds, spec/suite/case, explicit profiles and feedback controls: PASS |
| D-F1 | both CLI providers TERM/HUP/INT, durable record, public runs/trace/doctor and immediate next max_runs=1 save: PASS |
| D-31 adjacent | eight public asks held behind a localhost request barrier; exactly three active slots/saves and five safe FULL warnings, all eight primary answers successful; then three immediate sequential saves succeed: PASS |
| D-40–D-50 affected lifecycle | existing Claude/Codex normal/timeout/TERM/HUP/INT, child/grandchild, spawn/pipe-close/cleanup faults, unrelated-group protection, inherited ignore and SIGCHLD tests retained and PASS |
| D-34b | deterministic boundary tests, nine actual readiness-synchronized SIGINT probes and launcher help/version/usage: PASS |
| New user → ask → diagnostics | config path, missing-config journey, private synthetic config init, normal/verbose/debug default-off, explicit save, trace/doctor: PASS |
| Failure → diagnostics → manual retry | one localhost HTTP 500 request with safe saved error trace; second user-initiated invocation succeeds; exactly two requests, no hidden retry: PASS |

Focused commands use the prefix stated above:

| Modules | Tests / result / exit |
|---|---|
| `test_qg_qa1_error_context test_feedback test_benchmark test_orchestration test_retrieval test_config` | 243 PASS, exit 0, 2.814s |
| `test_qg_qa1_signal_persistence test_cli_provider_lifecycle test_cli_lifecycle_state test_diagnostics_ask_persistence test_diagnostics_failure_matrix test_diagnostics_store` | 128 PASS, exit 0, 53.250s |
| `test_entrypoint_interrupt test_cli` | 38 PASS, exit 0, 21.333s |
| `test_qg_qa1_journeys` | 4 PASS, exit 0, 6.673s |

## Full canonical checks and test provenance

All three commands ran from the canonical repository and checked the same final
implementation bytes. After logical commits, every tested file was compared
with its committed blob; results belong to implementation SHA above. No source
or test edit followed full checks.

| Actual Python | Command | Result |
|---|---|---|
| 3.13.3 checkout | `make -C tools/llmtool check PYTHON=/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/python` | exit 0; 965 tests, 144.945s; OK (skipped=1) |
| 3.9.6 Mac system | `make -C tools/llmtool check PYTHON=/usr/bin/python3` | exit 0; 965 tests, 221.704s; OK (skipped=1) |
| 3.14.8 local runtime | `make -C tools/llmtool check PYTHON=/private/tmp/llmtool-a11a7-py314-venv/bin/python` | exit 0; 965 tests, 136.033s; OK (skipped=1) |

The 3.14 path is an existing isolated interpreter, not a repository/worktree;
all tests still launched from canonical root. Each local skip is the existing
`test_git_intel.GitFixture.test_unusual_filename_bytes_roundtrip` because this
host filesystem/sandbox rejects non-UTF-8 filename bytes. No assertion was
removed or relaxed to obtain a pass. Linux CI must supply its own current result.

Local logs are ephemeral runtime evidence under /private/tmp, not repositories
or committed raw request/response dumps. Digests follow for reproducibility:

| Local log | SHA-256 |
|---|---|
| `llmtool-qa1-context-before.log` | `1bbb42aa9ea8b9d0cb2aad6cdbbc2173278df675e3e51577e6fdc516902ba0b1` |
| `llmtool-qa1-f1-before-final.log` | `fae80759a73c6e3713ecec5be04b282f19e15bda170928f7c7ddd1779cbdb44b` |
| `llmtool-qa1-startup-before-final.log` | `9e1e90bfe910704a25e279f037418e25dc91f4220f2122f84eb886185c1514c2` |
| `llmtool-qa1-context-focused.log` | `45def3de22cee175bc082125ab01ed3137fe141e0d3fd88a67170e7b2496ec02` |
| `llmtool-qa1-f1-focused.log` | `e5b7176c7ef2aa5c006dcd266fcf8829bda10f0581ffb71256e68c76489a29da` |
| `llmtool-qa1-startup-focused.log` | `4667c88944e2add28cb6d5652cabacb276483f47f07c03d7d50203b667b5ff7d` |
| `llmtool-qa1-targeted-journeys-final.log` | `a894e4431d437be4240b9fc581e403de82cfa52d10f409ec3469e950fdf14ea7` |
| `llmtool-qa1-required-py313.log` | `b628790758eb1d4a8f2543247bdf01b551bb848ef058b5b9dc5c730b103862d5` |
| `llmtool-qa1-required-py39.log` | `c897595767019ab9715a4bf8edbbb96ee405822c3eb605de5ab7b43a32cf1298` |
| `llmtool-qa1-required-py314.log` | `8bb95de8d58a976688c43452d9259d8112506d923b9e58d832cc134cfb453821` |


## Ordinary diff review, preserved boundaries and remaining gates

Coordinator ordinary review covered the entire production, test and README
change plus evidence provenance. No material unresolved issue was found in the
authorized fixes. This is not an independent Claude review or formal security
re-review. Syntax/unit canonical checks and `git diff --check` PASS.

- No retry/fallback or provider double-call, executor, shell/Git/file-mutation
  capability added. CLI fixed argv, shell=False, sanitized child environment,
  no unnecessary credential inheritance, Claude safe-mode/tools restriction,
  Codex restricted profile/network policy, isolation, bounded output/timeouts,
  raw-stderr/reasoning omission stay unchanged and covered by existing tests.
- Persisted data remains allowlisted metadata; new tests reject synthetic prompt,
  response, raw error body, secret and reasoning markers. No real credential,
  session/auth file, Keychain or user config was inspected/modified.
- HTTP TERM default disposition is explicitly preserved; HTTP HUP, real external
  provider/VPN/auth and native Windows were NOT TESTED this round.
- D-34a remains outside the controlled entrypoint guard. Optional legacy
  analyze-log/review-diff/context-pack small-output broken-pipe exit 120 unchanged.
- DG-2/DG-3, TR-1/TR-2/TR-3, DA-3, deferred M2/M4/M5, doctor P1–P4/N1,
  staging residual-capacity and abandoned-grace limitations unchanged.
- Nine pre-existing untracked benchmark/reasoning/case paths were preserved.
  No automatic cleanup of user artifacts or unknown store data was introduced.
- No alternate checkout, history rewrite, reset/stash/force push, QA gate signoff,
  main merge, or A11b/A11c work. Draft publication does not authorize merge.
- At report cutoff exact final-head CI is pending. Required final-head 3.9/3.14
  CI must be green before the delivery can say ready for Claude targeted re-QA.
  Independent targeted re-QA and ChatGPT final adjudication/merge decision remain
  external gates even after CI succeeds.

Local dispositions (new permanent regressions and full local checks passed):

Q-BUG-1: CLOSED (local fix/verification; independent targeted re-QA pending)

F1: CLOSED (local fix/verification; independent targeted re-QA pending)

D-34b: CLOSED (local fix/verification; independent targeted re-QA pending)

D-34a: PLATFORM LIMITATION / UNCHANGED

OPTIONAL BROKEN PIPE: UNCHANGED

READY FOR CLAUDE TARGETED RE-QA: NO at report cutoff (exact final-head CI pending;
publication handoff must record the subsequent actual result).
