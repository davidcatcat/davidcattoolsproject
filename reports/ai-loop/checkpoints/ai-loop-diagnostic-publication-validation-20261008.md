# AI-loop automatic diagnostic publication validation — 2026-10-08

Scope: standalone Mac development helper. No llmtool feature implementation or
Mac containment acceptance was performed in this coordinating Work environment.

David requested that the script publish reports/logs instead of requiring manual
ZIP transfer. The helper now attempts report publication after a model/test STOP.
The explicit `--publish-diagnostics RUN_ID` entry uploads an existing frozen ZIP
without models, tests, probe processes, local branch/index changes or recovery.

Destination: reports-only `automation/ai-loop-diagnostics`, immutable per-run
paths under `reports/ai-loop/diagnostics/`. The branch starts without a source
parent. Existing records are preserved; repeat identical data is read-only.
Conflicts/concurrent moves fail without force or automatic retry. Tree/remote blob
identities are verified before publication is reported as successful.

Published material is bounded runner/provenance projections, capture hashes, the
Git-intended validation report and native Mac evidence/mandatory logs. Raw provider
streams/free-form summaries/findings/stop messages, candidate source snapshots,
credentials and unrelated files are excluded. Original ZIP and STOP remain local;
upload failures do not overwrite test/model failure status.

Validation:

- 138 offline helper fixture tests: PASS.
- Existing 127 fixtures plus 11 publication/CLI tests: PASS.
- Frozen capture hash/contract checks, omission sentinels and archive drift: PASS.
- Reports-only creation, preservation, idempotence, conflicting records: PASS.
- Concurrent remote move and post-publication mismatch rejection: PASS.
- Structured request file handling, HTTP 404 versus 403, receipt symlink: PASS.
- Original STOP preservation when upload/auth fails: PASS.
- Upload-only CLI bypasses models/tests/environment/recovery and rejects mixed flags: PASS.
- Python 3.9 grammar check: PASS (not an actual Python 3.9 runtime run).
- Existing launcher Bash syntax and changed-file whitespace: PASS.
- Real Mac/GitHub CLI publication: PENDING David's upload-only invocation.
- llmtool native hung-supervisor fix/acceptance: NOT PERFORMED; remains blocked.
- Independent security approval, merge and A11b.2.2.2: NOT AUTHORIZED.

The existing launcher fetches the updated single-file runner, so no launcher change
or reinstall is required. The latest diagnostic-only command is:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --publish-diagnostics 20261007T162747Z-96025
```

This publication feature does not authorize a new native recovery. The consumed
87533 continuation remains historical; the 96025 hung-supervisor failure still
requires coordinator assessment and a matching recovery contract.


## Actual Mac publication follow-up — 2026-10-07

David executed `--publish-diagnostics 20261007T162747Z-96025` and the helper
reported DIAGNOSTICS ONLY COMPLETE. The coordinator fetched the resulting
immutable commit `3da0c0c25d0695fcb3792a225d503d372fc52572` and verified the
published native JSON against the original ZIP. Real Mac/GitHub CLI diagnostic
publication: PASS for this invocation. No task/model/test/branch/index change or
recovery claim accompanied the upload. The earlier PENDING row is historical.
Native containment remains FAIL at hung_supervisor; its bounded repair is now
separately authorized by CURRENT task.md and the coordinator assessment.
