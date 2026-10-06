# llmtool Round 3 — second C1/C2 remediation offline retest handoff (2026-10-06)

## Purpose

Run a second fresh full offline Python 3.9 retest after fixing:
- C2 raw-regex double escaping;
- stale request-proof exception expectation.

Canonical repo only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-round3-format-compliance-20261006`

Expected remote head:

`b52c2f76e793196c8427b348647d4891ba4c2c4b`

No `/private/tmp/*` worktrees/clones.

## Required execution

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
git diff --check
```

Expected static test-method count: **88**.

If count differs, explain and do not silently treat as PASS.

## Required checks

C1:
- anchor-stage L03 evaluation rejected by accounting;
- wrong-lane evaluation rejected;
- failed final evaluation cannot be overridden;
- plain dict cannot be request proof;
- 3/7 L03 cannot yield gate_passed=True.

C2:
- colon-form canonical fields fail;
- partial/malformed EVENT claims fail;
- benign standalone EVENT prose is extra/pass;
- fixture Canonical EVENT comment is extra/pass;
- EVT-/REQ- namespace prose fails rather than becoming harmless extra.

## Fresh report

Create:

`reports/llmtool_token_savings_round3_format_probe_offline_retest2_20261006.md`

Record exact tested head, Python version, test totals, exit code, git diff --check,
artifact SHA-256 values, fixture hashes, L03 multimap cardinalities, and explicit
C1/C2 case results.

Company live calls must remain 0.
Codex calls must remain 0.
No production/runtime/skill/fixture/gold changes.

If and only if all 88 tests pass, mark:

`Ready for targeted remediation review: YES`

Then commit/push the report and STOP.

Do not implement or run the live wrapper.
