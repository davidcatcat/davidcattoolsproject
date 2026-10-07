# AI-loop classification diagnostics — 2026-10-08

Helper base: `98b1871be51128da8dcdcf6eb32c27e2af89fa14`.
Scope: standalone development helper/schema/diagnostics and offline fixtures only.
No llmtool implementation, task acceptance, repair budget or roadmap change.

## Observed Mac run and evidence

Run `20261007T183516Z-7365` stopped at candidate
`474679ecda36da610a96e7ec84da39a5dd3ae33b`.
Its immutable terminal diagnostics are at
https://github.com/davidcatcat/davidcattoolsproject/blob/eeff42cab1efeb7a67e9cfd5c324c61f1bf10368/reports/ai-loop/diagnostics/20261007T183516Z-7365/index.md

Published rounds show:

- Round 0: related/ordinary review PASS; full-final FAIL. Read-only Claude classified
  a test's cwd-dependent evidence assertion as TEST_BUG; guidance/disposition were
  published and Codex repaired the test. No production cwd preflight was weakened.
- Round 1: related/ordinary review and both full suites PASS; native hung_supervisor
  FAIL in `unresolved`. Completed subcases include `closed_status`, which now
  observed status 2 followed by two SZOMB=5 rows. Claude classified the controller
  overwriting its authoritative supervisor identity with fault-injected micros as
  TEST_BUG; guidance/disposition were published and Codex prepared a test repair.
- Round 1 failed-fixture cleanup records RESOLVED, host-only SIGKILL, decision
  before reap, and host/supervisor/group/member absence. This is controller cleanup
  evidence, not production containment acceptance. Later scenarios did not run.
- Round 2: related/ordinary review PASS; full-final failed on `/usr/bin/python3`
  (exit 2), while the venv full command exited 0. No new native gate ran in round 2.
  Failure publication succeeded; Claude returned, but classification validation
  stopped with the compound message `invalid failure classification or evidence binding`.

The rejected classifier's raw output and round-2 full test logs are not in the Git
projection. The precise invalid field and the Python 3.9 test error are therefore
UNKNOWN in this Work session. Do not infer a hash mismatch or oversized summary
from the compound message. Historical native evidence belongs to round 1, not 2.

## Helper correction

The request schema now encodes the same existing runtime limits: 2000 characters
per summary/finding string, 16 findings, and exact expected HEAD/evidence digest.
The validator retains all previous rejections and emits fixed field-specific reason
codes. It never truncates a response, substitutes a digest, accepts optional-only
repair advice or retries an invalid provider output.

Rejected outputs are projected as an immutable separate `round-N-rejection` batch:
expected identities, raw/evidence hashes, reason codes, match booleans, string lengths
and findings count. Raw provider text, arbitrary model fields, summaries/findings,
session data, credentials and deterministic log text are not published by this path.
The candidate/evidence must remain unchanged before an automatic rejection upload.

`--publish-diagnostics RUN_ID` can supplement already published terminal diagnostics
using original local stopped-run artifacts and the original Git-pinned contract.
It preserves the ZIP and existing report paths; the rejection batch is immutable
and idempotent. It calls no AI/test/process-signal/recovery path and changes no local
feature HEAD/index. Missing, symlinked, oversized, wrong-run or changed inputs stop.
This upload does not authorize another repair or reset any budget.

## Validation and limits

Standalone offline helper suite: **176 tests PASS**, including prior 167 fixtures
and new classification/rejection publication cases. Tests cover specific length
vs identity reasons, valid-output exclusion, malformed envelope/unknown fields,
no private-text leakage, original-contract post-stop upload, unsafe/oversized/wrong
provenance rejection, immutable/idempotent Git batches, and automatic publication
before a strict stop without another model/repair call.

Python 3.9 grammar, helper compilation, Bash launcher syntax and whitespace checks:
PASS. All checks used isolated standalone helper files and synthetic fixtures;
no project clone/worktree or llmtool/Mac/native acceptance execution occurred here.
Actual Mac upload and classifier schema behavior remain PENDING. Full/native gates,
Draft PR, independent security review and READY_FOR_FINAL_REVIEW remain incomplete.

Next permitted Mac action: supplement diagnostics for `20261007T183516Z-7365`.
Do not blindly rerun or resume the feature task before assessing the exact rejection
and remaining Python 3.9 failure evidence. No merge or A11b.2.2.2 is authorized.
