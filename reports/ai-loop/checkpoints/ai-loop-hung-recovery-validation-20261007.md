# AI-loop explicit hung-supervisor recovery validation — 2026-10-07

Control base: `412c0ffa0fe3be6c408c2d6cac7b170d44d203c0`.
This is standalone helper/contract work; no app checkout or Mac execution was
performed in this Work environment. David runs the canonical Mac repository.

Changes:

- Optional native recovery `failure_scenario` is validated against fixed native
  scenario names. Omission preserves historical sigint behavior; mismatch,
  unknown/non-string values and extra policy keys fail closed.
- Recovery prompt names the explicitly assessed scenario instead of assuming
  SIGINT. Exact candidate/evidence/scope/PID/one-use checks remain intact.
- CURRENT contract pins run 96025, candidate a00eda49 and original summary/native
  digests; permits bounded non-reaping transition observation and test subcase
  diagnostics, retaining intentional closed-status EOF and strict SZOMB/identity checks.
- Historical implementation requirements are retained verbatim. Task text was
  shortened by moving superseded recovery instructions to historical evidence.
- Scope/allowlist/tests/budgets/acceptance gates outside the recovery policy are
  identical to the prior control. All native gates run fresh after changed bytes.
- Actual Mac report-only publication is recorded separately from containment FAIL.

Validation:

- 143 offline helper fixture tests: PASS (prior 138 plus five recovery tests).
- Explicit hung recovery, no inherited signal PASS, scenario mismatch rejection:
  PASS with mocked filesystem/Git/native records.
- Previous native policy continuation and historical sigint compatibility: PASS.
- Unknown/type/extra-key rejection and exact pinned scope/gate guards: PASS.
- Full pipeline explicit hung prompt and related/full/mandatory gate order: PASS.
- Existing no-native-retry, two-call bound and unchanged-byte rejection: PASS.
- Python 3.9 grammar: PASS; no actual Python 3.9 runtime claim.
- Bash launcher syntax, changed-file whitespace and retained contract bytes: PASS.
- Mac/application containment repair or native tests here: NOT PERFORMED.
- Fresh operator/source evidence: failed probe is deliberate closed_status EOF.
- Exact transient timing and corrected native cleanup: still PENDING Mac verification.
- Application acceptance/security/merge/A11b.2.2.2: NOT APPROVED.

Operator must freshly inspect possible retained host 5769/supervisor 5770 and
resolve fixtures before one-use `--resume 20261007T162747Z-96025`. Saved numeric
IDs do not authorize signals. No new launcher installation is needed.

## Correction after fresh operator ps

Fresh host argv identifies closed_status. Source verifies intentional writer close
and self-group SIGKILL within the hung_supervisor acceptance scenario. The contract
was narrowed before operator resume: no speculative exception/protocol diagnostic
expansion, no TERMINAL requirement for intentional EOF. Existing 143 helper fixture
results apply to unchanged helper code; this correction changes documentation only.
Pinned recovery policy, candidate/evidence bytes and gates remain unchanged.
