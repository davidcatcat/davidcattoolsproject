# llmtool Round 3 — Phase-0 live execution handoff final review (2026-10-06)

## Verdict

**PASS — exact live Company Phase-0 execution handoff may be authorized.**

No Company request was sent during this review.

## Exact reviewed handoff revision

Reviewed handoff commit:

`7569a8f0f9dc175bcf56229d590e950c9cbdee6e`

Reviewed file:

`reports/llmtool_token_savings_round3_phase0_live_execution_handoff_20261006.md`

Frozen tested implementation remains:

`3325f75e954a4099e31c5936412307320974cb4f`

Reviewed manifest pin remains:

`37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`

The handoff-only hardening commit did not modify:
- `reports/token_benchmark/20261006_round3/*`;
- preregistration;
- instructions;
- fixtures/gold;
- production/runtime/skills.

## Final-review checks

PASS:
- literal reviewed manifest pin is hard-coded; it is not derived from the manifest;
- no direct Company smoke/curl precedes L02-1;
- canonical Mac repo only;
- final offline 88/94 regression preflight remains mandatory;
- exact logical order is L02-1,L03-1,L02-2,L03-2,L02-3,L03-3;
- one serial Phase0Wrapper instance;
- no retry/fallback/max-token mutation;
- raw stdout directory is outside tracked repo;
- PASS requires six PASS records plus complete accounting and gate_passed=true;
- paired Codex benchmark cannot start automatically;
- raw model text is excluded from the state/report publication path.

## Final driver hardening

The initial execution-handoff review found one execution-level forensic weakness in
state persistence: the first draft unlinked the previous `phase0_state.json` before
writing/replacing the next state. That created a small crash window with no durable
state, and a snapshot failure could also remove the last good state.

The handoff was corrected before authorization.

The reviewed driver now:
- never unlinks the previous durable state before replacement;
- writes a process-specific private temporary file;
- fsyncs the temporary state file;
- atomically replaces `phase0_state.json` with `os.replace`;
- cleans only leftover temporary state files;
- preserves the previous durable state if a new snapshot fails;
- does not mask the original execution error if final snapshotting also fails.

The embedded Python driver was syntax-checked after the change.

## Hard-process interruption handling

Before every potentially consuming trial, the driver now writes:

`run_status = BEFORE_TRIAL`

with the exact `next_trial`.

Therefore a SIGKILL/OS crash/power loss that bypasses Python exception handling has a
conservative recovery rule:

- never resume/retry the existing run;
- inspect only the last private `phase0_state.json`;
- if it says BEFORE_TRIAL, treat that trial as possibly emitted/consumed;
- classify accounting as INCOMPLETE / UNKNOWN;
- never use known_cumulative as a complete total;
- do not create run2 without a new coordinator decision.

This rule intentionally prefers unknown accounting over a false token-savings claim.

## Authorization boundary

The handoff is now suitable for explicit live authorization.

Live execution must still use the exact literal:

`37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`

and the exact private raw directory:

`/Users/davidsun/.llmtool-round3-phase0-live/37f48c17-run1`

The operator must confirm Company VPN connectivity before running, but must not perform
a throwaway Company inference/smoke request. The first model request is L02-1.

## Final status

```text
PHASE-0 LIVE EXECUTION HANDOFF FINAL REVIEW:
PASS

Implementation blockers:
NONE

Execution-handoff blockers:
NONE

Manifest pin:
37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404

Helper regression baseline:
88/88 PASS

Wrapper regression baseline:
94/94 PASS

Company live calls so far:
0

Codex benchmark calls:
0

May explicitly authorize Company Phase-0 live run:
YES

May start paired Codex benchmark:
NO — Phase-0 result must be reviewed first
```
