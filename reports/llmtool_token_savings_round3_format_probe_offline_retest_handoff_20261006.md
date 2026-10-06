# llmtool Round 3 — C1/C2 remediation offline retest handoff (2026-10-06)

## Purpose

Re-run the full Round-3 offline deterministic suite after the independent review
remediations for C1 and C2.

No live provider calls are authorized.

## Repository

Canonical repo only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-round3-format-compliance-20261006`

Expected remote head:

`398e74c75ed225bb7d636cdaa7dd16d8713d7ead`

Never use `/private/tmp/*` worktrees/clones.

## Scope

Allowed:
- sync the canonical branch safely;
- run the offline Python 3.9 suite;
- run git diff --check;
- verify fixture hashes/multimap counts;
- publish a fresh retest report only.

Forbidden:
- company LLM call;
- Codex call;
- production/runtime/skill changes;
- fixture/gold changes;
- live wrapper implementation;
- merge.

## Required commands

From canonical repo:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
git diff --check
```

Record:
- exact tested HEAD;
- Python version;
- test count;
- pass/fail/error/skip;
- exit code;
- format_probe.py SHA-256;
- test_format_probe.py SHA-256;
- fixture digests;
- L03 multimap counts.

Expected static test count before execution: **88**.

Do not convert a different test count into PASS without explaining why.

## Required C1/C2 assertions

The rerun must include and pass the new counterexamples for:

### C1
- L03 anchor-stage FrozenEvaluation rejected by accounting;
- wrong-lane evaluation rejected;
- failed final evaluation cannot be overridden by caller reasons;
- arbitrary dict cannot be used as request proof;
- 3/7 L03 cannot produce gate_passed=True.

### C2
- colon-form fabricated canonical fields fail;
- benign prose containing standalone EVENT remains an extra and passes;
- frozen fixture Canonical EVENT comment remains an extra and passes;
- EVT-/REQ- namespace prose is not silently accepted as a harmless extra.

## Fresh validation report

Create:

`reports/llmtool_token_savings_round3_format_probe_offline_retest_20261006.md`

The report must explicitly supersede the historical 79/79 validation for the current
implementation head.

Required summary:

```text
Exact tested head:
Python:
Tests: PASS x / x
FAIL:
ERROR:
SKIP:
Exit:
git diff --check:

C1 remediation tests:
PASS / FAIL

C2 remediation tests:
PASS / FAIL

Company live calls:
0

Codex calls:
0

Production/runtime/skill changes:
NONE

Ready for targeted remediation review:
YES / NO
```

Commit/push the report only after the fresh run.

Then STOP.

Do not begin live Phase 0.
