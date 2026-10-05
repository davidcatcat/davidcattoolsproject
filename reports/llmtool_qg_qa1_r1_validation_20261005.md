# llmtool QG-QA1 R-1 validation — 2026-10-05

## Task Contract v1

- ID: QG-QA1-R1-20261005, version 1; canonical Mac repository only.
- Repository: davidcatcat/davidcattoolsproject.
- Branch: fix/llmtool-qg-qa1-required-bugs-20261005; continue Draft PR #31.
- Fetched origin/main/base: fe169aff967f4e7329128cf434c93ff3a3002b30.
- Previous PR HEAD: 8fb501fa9a84c58479a2e73d14f5d0f8b0206e3d.
- Authorized: R-1A shared file-reader context propagation for orchestration plan
  and benchmark spec/case/suite/runtime/output/entry files; R-1B benchmark-owned
  candidate-output validation context. Use only fixed caller-owned labels.
- Acceptance: failing permanent public CLI tests before production fix; original
  feedback wording preserved; missing/nonregular/oversized/UTF-8/traversal/symlink
  rejection unchanged; exact file/output bounds and regression pass/fail unchanged.
  No raw paths/input/traceback; invalid offline input makes zero provider calls.
  Existing parser/profile, F1 and D-34b permanent tests stay PASS. Focused and full
  canonical checks on checkout Python, actual 3.9 and available 3.14; exact final-head
  GitHub 3.9/3.14 Offline canonical checks and Working tree is clean SUCCESS.
- Forbidden: F1/D-34b/D-34a/DG-1/HTTP signal or launcher changes, Y-F1, optional
  broken pipe, N-1/N-2/generic UX, parser/safety/assertion semantics, A11b/A11c,
  retry/fallback/refactor, historical Claude report modification, unrelated user
  artifacts, alternate repository/clone/worktree, reset/stash/force/rewrite/merge.
- Delivery: coherent implementation commit plus report-only commit, normal push
  existing branch, update existing Draft PR #31, no new PR/merge. Final publication
  SHA/CI identity recorded in PR and handoff per WORKFLOW. Stop for Claude final
  targeted re-QA. Coordinator is the sole source/test/Git writer in this Mac round.

Status: R-1 local regressions and full checks PASS. Final report-head CI and
Claude final targeted re-QA remain pending at this report cutoff. This round
does not declare the formal QG-QA1 gate passed or authorize merge.


## Evidence identity

- Actual canonical Mac root: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Fetched main/base: `fe169aff967f4e7329128cf434c93ff3a3002b30`.
- Previous PR HEAD: `8fb501fa9a84c58479a2e73d14f5d0f8b0206e3d` (Claude re-QA report commit).
- Branch: `fix/llmtool-qg-qa1-required-bugs-20261005`; existing Draft PR #31.
- Tested implementation SHA: `c2662ad3cbd4428d678a8a23853d3c878dda496d`.
- Contract body SHA-256: `b3f0eeb275956a10a897ec6c54f3c13c8d68462a02c0fa5f11ff069908f6cb6c`.
- Six-file tested manifest SHA-256: `45edde607658eeea4c159052a7c64b8f767186fccfd14056149baa85e684c110`.
  Sorted repo-relative UTF-8 path + NUL + file bytes + NUL, concatenated and hashed.
  Every individual tested digest matches the working file and implementation blob.
- Final report-only HEAD / exact-head GitHub CI are recorded once in PR #31's
  publication description/Checks and delivery, per WORKFLOW. No recursive report
  commit solely to embed its own SHA. At this cutoff new final-head CI is pending;
  old PR CI from b2a30a7 is historical and cannot satisfy this round's gate.
- Coordinator performed implementation, tests, ordinary diff review and Git work.
  No independent agent/model/cost claim. Claude final targeted re-QA is pending.

## Changed files

Production (five):

- `tools/llmtool/llmtool/feedback_files.py`
- `tools/llmtool/llmtool/feedback.py`
- `tools/llmtool/llmtool/benchmark.py`
- `tools/llmtool/llmtool/benchmark_cli.py`
- `tools/llmtool/llmtool/orchestration_cli.py`

One new test file: `tools/llmtool/tests/test_qg_qa1_file_context.py` (16 methods).
One new documentation file: this report. No existing test assertion was removed
or relaxed. Historical Claude re-QA/formal QA and round-1 validation stay unchanged;
README/ROADMAP/WORKFLOW/skills/config/lifecycle/entrypoint/CI workflow unchanged.

## Before-fix reproduction

The permanent public CLI tests were added first and executed before production
changes on previous HEAD 8fb501fa. All tests launch from canonical root, use
synthetic temporary files/HOME and call the public cli.main entry. Provider
factories are patched and required to have zero calls; no store/HOME mutation.

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_qg_qa1_file_context
```

Actual Python 3.13.3; authoritative log
`/private/tmp/llmtool-qa1-r1-before-final.log`: **16 methods, 75 failed subcase
assertions, exit 1, 4.720s**. Feedback wording, exact 1 MiB acceptance and normal
pass/fail controls already passed; incorrect domain labels failed.

| Reproducer path | Expected | Actual before fix |
|---|---|---|
| `orchestrate --dry-run --plan` oversized file | orchestration plan file exceeds 1 MiB, rc1/empty stdout | feedback file exceeds 1 MiB, rc1/empty stdout |
| plan missing/directory/bad UTF-8/traversal/symlink | orchestration-owned file error | feedback file/input error |
| benchmark spec/case/manifest/runtime/output and suite entry file errors | respective benchmark-owned label | feedback file/input label |
| benchmark evaluate/suite output ESC/control | benchmark output text contains unsupported controls | feedback text contains unsupported controls |

An initial harness run expected directory errors to say regular-file-required.
Current CPython rejects the directory at fdopen and emits the original fixed
file-operation error instead. The expected suffix was corrected BEFORE production
changes and the authoritative baseline rerun; no rejection behavior was altered.
FIFO separately verifies the existing regular-file-required wording.

## Minimal implementation and context map

The existing reader is reused. `read_feedback_text`, `_parent` and `_read` take
keyword-only context='feedback', propagated only into fixed error messages.
Existing feedback read/write callers keep the default, so A7 messages remain
byte-for-byte identical. File operations, flags, branches, limits and exception
categories are unchanged.

`evaluate_regression` similarly defaults to feedback and forwards context only
to its existing bounded text validator. `evaluate_benchmark` supplies the fixed
benchmark output label, shared by evaluate/live/suite. No assertion or benchmark
pass/fail logic changed.

| Public owner | Fixed code-selected label |
|---|---|
| feedback readers / regression evaluation | feedback (unchanged default) |
| orchestration --plan | orchestration plan |
| benchmark evaluate/live --spec | benchmark spec |
| benchmark evaluate/live --case / suite case entry | benchmark case |
| benchmark --manifest | benchmark suite |
| benchmark --runtime-file | benchmark runtime |
| benchmark --output-file / suite output entry | benchmark output |
| benchmark candidate-output validation (evaluate/live/suite) | benchmark output |

Labels never originate from filename, JSON, user text, profile or environment.
The spec/case choice selects between two constants. No context CLI/config field
or new capability was introduced. N-1/N-2 parser/UX observations remain untouched.

## Permanent regression and strictness results

New public matrix covers seven failure types per file owner: missing, directory,
file >1 MiB, invalid UTF-8, parent traversal, leaf symlink and parent symlink.
Owners include feedback, plan, spec, case, manifest, runtime, output, live spec/case,
suite case and suite output. Every rejected command requires rc1, empty stdout,
exact safe message, no feedback in non-feedback errors, no traceback/raw path/
private marker, zero provider factories, absent store and unchanged empty HOME.

- `test_feedback_file_errors_keep_exact_original_wording`: original feedback
  file operation/exceeds/UTF-8/traversal messages preserved, PASS.
- `test_orchestration_plan_file_errors_use_orchestration_domain`: all seven PASS.
- `test_benchmark_{spec,case,suite_manifest,runtime,output}_file_errors`: PASS.
- `test_benchmark_live_spec_and_case_fail_before_provider_call`: PASS, zero calls.
- `test_suite_case_entry_file_errors`, `test_suite_output_entry_file_errors`: PASS.
- `test_benchmark_candidate_controls_use_output_context`: ESC/NUL/BEL all rejected
  as benchmark output; `test_suite_candidate_controls_use_output_context`: PASS.
- `test_feedback_regression_controls_keep_feedback_context`: unchanged A7 label, PASS.
- `test_fifo_regular_file_requirement_unchanged`: feedback/plan/output reject
  nonregular file with their appropriate domain, PASS.
- `test_exact_file_limit_is_still_accepted_for_every_domain`: valid padded plan,
  spec/case, manifest, runtime and candidate output exactly 1 MiB accepted, PASS.
- `test_evaluation_pass_fail_and_whitespace_semantics_unchanged`: matching,
  nonmatching, whitespace/tab/newline and empty candidates preserve feedback and
  benchmark pass/fail, PASS.

Source review also compared AST against 8fb501fa after normalizing only context
keyword declarations/arguments and replacing context+literal suffix with the
original feedback prefix. All FIVE production files are otherwise AST-identical.
This checks the validation/control-flow branches remained the same; it is
supplemental to public behavioral tests, not a replacement for them. Existing
strict parser/JSON depth/duplicate/schema/list/control/security tests also PASS.

## Focused and adjacent checks

Actual checkout Python 3.13.3; commands ran from canonical root:

```sh
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_qg_qa1_file_context test_feedback test_benchmark test_orchestration test_qg_qa1_error_context
PYTHONPATH=tools/llmtool:tools/llmtool/tests tools/llmtool/.venv/bin/python -m unittest -v test_qg_qa1_signal_persistence test_entrypoint_interrupt
```

- R-1 plus feedback/benchmark/orchestration/prior parser/profile: **166 PASS,
  exit 0, 6.873s**. Prior malformed plan/schema/>8 steps/spec/suite/profile/active
  wording controls remain covered and PASS.
- Existing permanent F1 + D-34b: **18 PASS, exit 0, 19.014s**, including both fake
  CLI providers' TERM/HUP/INT cleanup/durable summary/immediate next save and
  readiness-synchronized real startup SIGINT probes.
- `cli.py`, `entrypoint.py` and `providers/cli_lifecycle.py` bytes compared with
  previous HEAD: unchanged. No HTTP TERM/HUP or signal/launcher implementation edit.

## Full canonical checks

The same frozen implementation bytes were tested in all three environments;
committed implementation blobs were checked against the tested manifest.

| Actual Python | Command | Result |
|---|---|---|
| checkout 3.13.3 | `make -C tools/llmtool check PYTHON=/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/python` | exit0; 981 tests in 142.741s; OK (skipped=1) |
| Mac system 3.9.6 | `make -C tools/llmtool check PYTHON=/usr/bin/python3` | exit0; 981 tests in 223.987s; OK (skipped=1) |
| local runtime 3.14.8 | `make -C tools/llmtool check PYTHON=/private/tmp/llmtool-a11a7-py314-venv/bin/python` | exit0; 981 tests in 130.482s; OK (skipped=1) |

The existing 3.14 interpreter is runtime-only, not an alternate checkout; all
commands/tests ran in canonical repo. Each local skip is the existing non-UTF-8
filename roundtrip rejected by this host filesystem/sandbox. No skip is PASS.
Full suites also reran all previous lifecycle/concurrency/security regressions.
Final-head Linux CI must independently pass both versions and clean-tree steps.

## Local evidence digests

Logs are runtime evidence under /private/tmp, not repository copies or real
provider/credential dumps. SHA-256 digests:

| Log | SHA-256 |
|---|---|
| `llmtool-qa1-r1-before-final.log` | `d96345d4c74a982b318fdbea28b5aa077cd2840dad1738aab0ae21a733dd0627` |
| `llmtool-qa1-r1-focused.log` | `6bb079ad7369c1ed2e497bf331b0e032e34cd68e4ac1230aa1984665760f0596` |
| `llmtool-qa1-r1-adjacent.log` | `cd3ee4f230d830e55c423b3495333e34858bc0243ae7ca99ed46cf257461da81` |
| `llmtool-qa1-r1-py313.log` | `9fea341b9084742c6d318ab3eb4a0d74a90f4b45695fa7fdd1e709316080232c` |
| `llmtool-qa1-r1-py39.log` | `62fc28bf896e2bf5b4f779b0f3e683c8711fb67d7d59c751466157199d0ba24c` |
| `llmtool-qa1-r1-py314.log` | `933b56ece5bc733b5223648909e6d611a563ded92496d90f65b1143a3d1bf00d` |


## Review, preserved scope and remaining gates

Ordinary coordinator diff review covered all five production files, the complete
new tests, strictness evidence and report. No unresolved material issue found
within R-1. `git diff --check` PASS. No external re-QA PASS is claimed here.

Nine baseline untracked benchmark/reasoning/case paths were preserved. No user
config/auth/credential inspection or mutation, cleanup of foreign data, alternate
checkout, force/reset/stash/rewrite or merge. No retry/fallback/provider double
call, generic parser/refactor/signal manager, A11b/A11c or new execution capability.
Historical Claude REQUEST CHANGES and prior validation evidence remain intact.

F1/D-34b and DG-1 remain unchanged and tested. D-34a, Y-F1 tiny launcher window,
HTTP default signals, optional legacy broken pipe, N-1/N-2, DG-2/DG-3 and other
previously deferred findings remain unchanged. Real provider/VPN/API/auth and
native Windows/manual Linux QA NOT TESTED this round; fake/local regressions
and Linux CI do not represent real inference. Full historical 271-case QA was
not rerun, and only Claude can supply the independent final targeted re-QA gate.

At report cutoff final report-head CI is pending. The publisher must record its
exact HEAD, Python 3.9/3.14 job and Offline canonical/clean-tree SUCCESS in the
existing PR publication record and final delivery before readiness becomes YES.

Q-BUG-1 R-1A FILE CONTEXT: CLOSED (local implementation/regressions)

Q-BUG-1 R-1B BENCHMARK OUTPUT CONTEXT: CLOSED (local implementation/regressions)

FEEDBACK CONTROL REGRESSION: PASS

F1: UNCHANGED

D-34b: UNCHANGED

READY FOR CLAUDE FINAL TARGETED RE-QA: NO at report cutoff (final report-head CI
pending; see subsequent exact-head publication record for actual final status).
