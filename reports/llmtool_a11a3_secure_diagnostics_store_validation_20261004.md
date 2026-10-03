# A11a.3 — User settings and secure POSIX store validation — 2026-10-04

Current external-review checkpoint: R1/R2 fixed, latest main integrated, local and
implementation-head CI PASS. Required company inference is SKIPPED-CONFIG because
no matching configured profile exists; actual CLI failure-path no-write PASS.
**A11a.3 external-review remediation NOT complete / required live verification blocked.**
**A11a.4 NOT STARTED.** See the appended remediation evidence for current provenance;
the original results below are historical and do not describe the current parser.

## Historical baseline — initial implementation c54aea58

Status: **IMPLEMENTED / LOCAL VALIDATION PASS / EXTERNAL REVIEW AND CI PENDING**.
The evidence below records the canonical working tree before its delivery commit/push.
A11a.3 is not merged/DONE;
A11a.4–7 and A11b/A11c are NOT STARTED.

## Recovered interruption and tested checkout

- Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `feat/a11a3-secure-diagnostics-store-20261003`.
- Base/HEAD: `0d324038ac44ac9ca1c9b32a11fb9cc163e12a12`, local merge of PR #16,
  A11a.2 ephemeral ask diagnostics. All implementation changes below are working
  tree changes on that HEAD, rather than a new committed revision.
- Local upstream: `origin/main`; local tracking revision equals HEAD, ahead/behind
  0/0 at validation time. No fetch was performed during validation, so this is not
  a claim of remote synchronization.
- Initial interruption state: config validation and configure preservation edits,
  four added store error codes, untracked diagnostics_settings.py and
  diagnostics_summary.py. No store implementation or A11a.3 tests/report existed.
- Pre-change pwd/root/branch/status checks matched the canonical repository.
- Python 3.13.3 interpreter:
  `tools/llmtool/.venv/bin/python`; imported llmtool.__file__ points to
  `tools/llmtool/llmtool/__init__.py` in this exact checkout.
- Installed `tools/llmtool/.venv/bin/llmtool --version`: `llmtool 0.1.0`.
- Additional local interpreter: `/usr/bin/python3`, Python 3.9.6.
- Final production/test manifest SHA-256:
  `5bb03c6a7186fdb96dbad5b7c56c8b394b57783a9dd82ec685416db46057b2df`.
  Manifest includes the seven production files and five test files in the changed
  artifacts section. Hash input is sorted paths relative to tools/llmtool, each
  encoded as path UTF-8 + NUL + file bytes + NUL. README/ROADMAP/report are excluded.

All check commands were launched from the canonical repository. Normal test
temporary directories contain fixtures/runtime state, never project checkouts.
No worktree, clone, alternate project workspace or codex_cli.py edit was used.
The pre-existing untracked benchmark/reasoning reports remain untouched.

## Implemented scope and milestone boundary

Dedicated frozen DiagnosticsSettings validates the user-only diagnostics mapping
and hard bounds from spec §7. Explicit save/no-save overrides trusted user
persist_metadata; the default is false. No project or generic environment opt-in
exists. Legacy resolved config/config show output is unchanged. Configure
preserves the original validated partial mapping without inserting defaults.
Malformed diagnostics config fails before provider/credential access.

The pure home resolver validates absolute, control-free locations without shell
expansion, rejects parent traversal, and implements macOS/Linux/XDG defaults and
Windows reporting-only location rules. LLMTOOL_HOME selects a location, never
enables persistence. Current ask never resolves the diagnostics home.

RunSummary is immutable, metadata-only, schema/versioned, bounded and deterministic.
Required fields have strict types/ranges. Supported-version extra fields are
ignored after bounded JSON validation; newer versions are rejected. Internal
cleanup parsing rejects duplicate keys, controls, nonfinite numbers (including
JSON exponent overflow), invalid UTF-8 and structural/file bounds. These helpers
are not the future runs/trace public reader contract.

DiagnosticsStore construction/default-off start does no filesystem access. An
explicitly enabled start provides the standalone POSIX store API:

- root-anchored, directory-relative no-follow traversal; no symlink components,
  parent traversal or broad resolve-then-write operations;
- directories 0700/files 0600; existing effective-owner/private-mode checks;
  unsafe existing stores are rejected without chmod; regular files must have
  one hard link;
- bounded root-lock acquisition, run locks, no-clobber creation and at most three
  run-ID collision attempts;
- validated reservation with the full max_run_bytes budget, including conservative
  summary/reservation overhead; active data growth never reduces the charge;
- serialized bounded typed event appends and sequence/start/end validation;
- fsynced trace/summary files, atomic same-directory no-clobber publication,
  directory fsync where supported, then reservation release;
- cleanup only at enabled start; age, count and byte eviction of validated eligible
  runs; active locks always win; abandoned records have a one-hour mtime grace;
- unknown entries stay and consume capacity; malformed reservations prevent a new
  guaranteed reservation; symlinks are counted without following their targets;
- startup failure returns None; write/completion failure returns false; each failed
  start/writer retains one fixed safe warning. Writers have independent warnings.

The four added codes are used by implemented store failures, not speculative
executor taxonomy: DIAGNOSTICS_STORE_UNSAFE, DIAGNOSTICS_STORE_FULL,
DIAGNOSTICS_RUN_COLLISION and DIAGNOSTICS_WRITE_FAILED. Generic failures retain
DIAGNOSTICS_STORE_UNAVAILABLE. No raw exception/path/provider body is rendered.
The closed error-code test now explicitly lists all 22 supported values.

**A11a.3 does not connect the store to ask.** CLI save/no-save syntax is accepted,
mutually exclusive, with None as the unspecified parser value. Explicit CLI save
emits one DIAGNOSTICS_STORE_UNAVAILABLE warning explaining integration is pending;
primary failure output comes first. Normal/no-save/debug remain unsaved. User
persist_metadata is validated/preserved and available through the settings
resolver, but does not trigger ask writes yet. This makes the requested option
honest without starting the separately gated A11a.4 lifecycle integration.

Provider success stdout, task exit status and exactly one provider request survive
the pending-save warning. The standalone store never imports/calls a provider.
No retry/fallback/executor, runs/trace/doctor, raw prompt/output persistence or
shared operational log is introduced.

## Final local validation

| Check | Python | Discovered | Passed | Failures/errors | Skipped | Time |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Focused diagnostics suite | 3.13.3 | 145 | 145 | 0 | 0 | 1.607 s |
| Canonical compileall + complete offline suite | 3.13.3 | 752 | 751 | 0 | 1 | 53.991 s |
| Canonical compileall + complete offline suite | 3.9.6 | 752 | 751 | 0 | 1 | 82.949 s |

Both canonical commands returned exit 0. One inherited test is explicitly skipped:
`test_unusual_filename_bytes_roundtrip`, reason
`host filesystem/sandbox rejects non-UTF-8 filename bytes`.
The skip is not a pass. The source/test hash identifies the bytes used in the
final canonical runs; no source/test bytes changed between those runs and hash capture.

Commands (launch cwd is the canonical repository):

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_diagnostics*.py' -q
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

Final canonical logs:
`/private/tmp/llmtool-a11a3-py313.log` and
`/private/tmp/llmtool-a11a3-py39.log`.

Behavioral tests exercise actual POSIX files/permissions/locks and two simultaneous
processes, reservations across 16 active runs, count/age/byte eviction, active-lock
protection, abandoned grace/recovery, unknown data preservation, unsafe ownership
and permissions, symlinks/hardlinks, no-clobber publication, short writes, fsync/
permission/write failures, bounded lock wait, summary mismatch, independent writer
warnings, strict config trust, configure preservation and default-off no-I/O.
Persisted RunContext metadata excludes prompt, response, raw HTTP body,
Authorization, credentials, reasoning and diagnostics-home markers.

## Isolated manual CLI smoke

Temporary trusted user config enabled persist_metadata; LLMTOOL_HOME named an
absent temporary diagnostics directory. Patched Path.home selected the temporary
user config, without changing the user's real settings. One mock client returned
the exact stdout marker `SMOKE_A11A3`. This is **mock integration**, not real
inference/authentication/network validation.

| Mode | Exit | stdout | Provider calls | Diagnostics home created | Secondary warning |
| --- | ---: | --- | ---: | --- | --- |
| default | 0 | SMOKE_A11A3 + newline | 1 | no | none |
| verbose | 0 | SMOKE_A11A3 + newline | 1 | no | none |
| debug | 0 | SMOKE_A11A3 + newline | 1 | no | none |
| save | 0 | SMOKE_A11A3 + newline | 1 | no | one unavailable/integration-pending |
| no-save | 0 | SMOKE_A11A3 + newline | 1 | no | none |

## Failures discovered and resolved

1. New settings fixtures initially used `env` rather than the existing `environ`
   argument; corrected the fixtures. The new depth test exposed missing explicit
   structural limits in the summary cleanup parser; implemented depth/item/control
   validation and finite numeric rejection. No existing check was weakened.
2. Initial sandboxed Python 3.9 compileall was blocked from the host's default
   bytecode cache. Initial Python 3.13 canonical execution encountered two mock
   HTTP server bind permission errors (702 discovered, one skip). Approved reruns
   from the same canonical checkout resolved these environment limitations.
3. First unrestricted runs exposed a genuine simultaneous lock-file creation race
   on macOS: O_CREAT + O_NOFOLLOW could return ENOENT in one process. Reproduced
   using safe exception type/errno/static code-frame information, without exception
   strings. Exclusive creation followed by no-follow existing-file open now retains
   the same lock inode. Runs directory creation moved under the store lock. The
   two-process regression passes on both interpreters.
4. Review fixed reservation accounting so a concurrently growing active trace
   cannot reduce its capacity charge; all candidate descriptors are closed after
   age/count/byte deletion. Writer warnings were separated from store-start warnings
   to prevent one writer's failure contaminating another completed run.

## Review, documentation and outstanding gates

inspect-repo, implement-feature, debug-ci and review-change repository workflows
were applied. Complete production/test diff and new modules were reviewed against
the implementation spec and AGENTS.md. Local review found no further material
issue after the fixes above. This is not independent external review.

README/ROADMAP describe the actual local A11a.3 scope and A11a.2 merge from local
history. Historical A11a.1/2 reports/spec remain unchanged. Relative document links,
new settings example, milestone scope/status and git diff whitespace are validated.

Known limits: macOS actual storage tested; Linux actual backend and Python 3.14
require remote CI. Windows unsupported-backend behavior is simulated; no Windows
backend exists. Trace and summary are separate atomic publications; readers must
require summary as the completion marker. Failed publication temp files or
invalid/unknown records remain conservatively and may require explicit manual
inspection/removal. Locks are POSIX advisory and assume cooperating writers.

Remote CI, exact committed-head checks, live provider inference and external review
are **NOT TESTED / PENDING**, not PASS. Local validation preceded the user-authorized
delivery commit/push; publication does not imply remote CI success. No merge, live
provider call or external message was performed. Following AGENTS.md's milestone review gate,
stop here for external review; do not automatically start A11a.4 or merge main.

## Changed artifacts

Production, relative to tools/llmtool:
`llmtool/cli.py`, `llmtool/config.py`, `llmtool/configure.py`,
`llmtool/diagnostics.py`, `llmtool/diagnostics_settings.py`,
`llmtool/diagnostics_store.py`, `llmtool/diagnostics_summary.py`.

Tests, relative to tools/llmtool:
`tests/test_diagnostics.py`, `tests/test_diagnostics_runtime.py`,
`tests/test_diagnostics_settings.py`, `tests/test_diagnostics_store.py`,
`tests/test_diagnostics_summary.py`.

Docs: tools/llmtool/README.md, tools/llmtool/ROADMAP.md and this report.

## External review remediation — 2026-10-04

External review: **REQUEST CHANGES**. Only the two requested production findings
were changed; the reviewed store architecture remains in place. This work uses the
actual local Mac canonical repository, not a Cloud checkout. No branch creation,
rebase, force push, history rewrite, feature-to-main merge or A11a.4 work occurred.

### Revision and integration evidence

| Revision | SHA |
| --- | --- |
| Original A11a.3 implementation | c54aea58c60bc4ae9d7139bd5b49cc77683af3d8 |
| Old feature base | 0d324038ac44ac9ca1c9b32a11fb9cc163e12a12 |
| Latest origin/main, verified by fetch in this round | b719aadd99948596b683292ee0e37756406f48f7 |
| Main integration merge on existing feature branch | edbf1c410926100213c604630d76b1f96cf8d046 |
| Remediation implementation / actual Mac tested SHA | 918468e8f5ea858e49188bd847a8003a7bf24108 |

Branch: `feat/a11a3-secure-diagnostics-store-20261003`.
Initial comparison after fetch: feature/main ahead 1, behind 2. The normal
`git merge origin/main` completed without conflict. After implementation, origin/main
is an ancestor of HEAD, feature/main ahead 3, behind 0. The delivery report/README
commit adds documentation only; its final SHA/CI is checked after publication and
recorded in the final handoff. WORKFLOW.md permits such documentation commits to
cite the tested implementation SHA when runtime/config/tests are unchanged.

Read latest AGENTS.md, WORKFLOW.md and engineering skills before integration.
AGENTS.md, WORKFLOW.md, root README and updated engineering skills are byte-identical
to latest main after remediation. The llmtool README/ROADMAP retain main's
Cloud/local workflow and separately reflect A11a.1/2 DONE/merged, A11a.3 feature
branch/remediation, A11a.4 NOT STARTED and overall A11a NOT DONE. No unexpected
production/test integration conflict occurred.

Remediation source/test manifest SHA-256 (same 12-file encoding as baseline):
`365d280a81cea42bf21c45373328a063427dd76b58972f6316e25db7ddc0a1f3`.
All local-tested source/test bytes match the committed implementation. The installed
`tools/llmtool/.venv/bin/llmtool` reports 0.1.0 and imports this canonical checkout.

### Findings and disposition

| Finding | Disposition | Evidence |
| --- | --- | --- |
| R1 public no-op flags | ACCEPTED / FIXED | Removed both parser flags, persist_metadata parser default, persistence_warning and all calls. Both flags now exit 2 before RunContext/provider/store/filesystem creation. Help omits them. Internal True/False/None precedence remains. |
| R2 cleanup tie-break | ACCEPTED / FIXED | Sorting now uses (completion timestamp, run_id). Equal-ended_at count and byte pressure tests reverse enumeration and remove lexical a before b. Active c and unknown entry survive. |
| R3 latest main integration | ACCEPTED | Normal merge edbf1c41 integrates fetched b719aadd; Cloud/local workflow and milestone checkpoints reconciled. |
| R4 company live smoke | SKIPPED-CONFIG | Safe existing config/readiness view finds only default with unset base_url/model; no matching company profile. No inference or credential request was attempted. |
| R5 company LLMTOOL_HOME smoke | SKIPPED-CONFIG for company inference; PASS for actual no-write assertion | On exact implementation SHA, debug synthetic marker request exits 1 / CONFIG_PROVIDER_NOT_READY, zero provider.request events; absent temporary home remains absent. |
| R6 validation provenance/report | ACCEPTED / UPDATED | Historical results preserved; current merge/implementation/local/CI/Mac evidence appended, baseline pending CI resolved, remaining company prerequisite recorded without claiming live PASS. |

R1 restores cli.py byte-for-byte to latest main's ephemeral ask behavior. Normal/
verbose/debug output contracts and failure trace=not-saved remain unchanged. No
store-to-ask connection, new retries, fallback or provider switching was introduced.
The internal settings resolver remains unchanged with explicit internal option >
trusted user persist_metadata > false. Both true/false overrides and unspecified
None behavior have coverage; the user mapping is still validated and preserved.

R2 regression creates completed run IDs a*32 and b*32 with exactly equal
`2099-01-01T00:00:00.000Z` ended_at. b is created first and os.listdir is explicitly
forced to enumerate b before a. Both count and byte pressure remove a first, keep
b and retain active c*32 and unknown data. A safe in-memory replay of the historical
timestamp-only sort fails both subcases; the fixed implementation passes. Existing
one-hour abandoned grace, owner/mode/no-follow/hardlink safety, reservations,
atomic publication, failure containment and two-process concurrency tests pass.
No broad store refactor was performed.

### Local checks — actual Mac

All commands launched from the canonical root, with temporary runtime fixtures only:

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_diagnostics*.py' -q
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_cli.py' -q
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

| Check | Python | Discovered | Passed | Failure/error | Skip | Time |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Focused settings/store/summary/runtime/core | 3.13.3 | 146 | 146 | 0 | 0 | 1.316 s |
| Affected CLI | 3.13.3 | 31 | 31 | 0 | 0 | 16.303 s |
| Full canonical compileall + offline suite | 3.13.3 | 753 | 752 | 0 | 1 | 51.929 s |
| Full canonical compileall + offline suite | actual 3.9.6 | 753 | 752 | 0 | 1 | 80.700 s |

All final commands exit 0. The same inherited non-UTF-8 filename test is skipped
by the Mac filesystem; this skip is not PASS. The first sandboxed affected-CLI
attempt could not bind its local mock HTTP server (0 tests / one setup error);
approved canonical-root rerun passed. No test was weakened to avoid the restriction.
Logs: `/private/tmp/llmtool-a11a3-remediation-py313.log` and
`/private/tmp/llmtool-a11a3-remediation-py39.log`.

### Remote CI correction and implementation evidence

The baseline report's remote/exact-committed-head pending entries are historical:
[old run 37139821212](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37139821212)
was checked in this round and is SUCCESS for c54aea58, both Python 3.9/3.14 jobs,
offline canonical checks and working tree clean. It proves only the old implementation.

[Remediation implementation run 37141187577](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37141187577)
is SUCCESS and headSha exactly equals 918468e8f5ea858e49188bd847a8003a7bf24108:

| Job | Tests | Offline canonical checks | Working tree clean |
| --- | --- | --- | --- |
| Python 3.9 | 753/753 PASS; no skip; 18.143 s | SUCCESS | SUCCESS |
| Python 3.14 | 753/753 PASS; no skip; 21.847 s | SUCCESS | SUCCESS |

Final report/README publication must have its own exact-head CI checked; the final
handoff records its SHA and run URL/status. This implementation run is not presented
as proof of an untested later delivery HEAD. Neither CI proves company connectivity
or inference, and green CI does not authorize merge or satisfy missing required
Mac live verification.

### Required company verification and LLMTOOL_HOME

Expected existing profile: openai-compatible / `http://10.29.7.58:4000` / `gw`.
These are the requested target endpoint/model, not a claim that the profile exists.
Both the trusted-user view and current runtime redacted config show only active
default, type openai-compatible, base_url=null and model=null. Matching profile:
none. R4 normal, verbose and debug are each **SKIPPED-CONFIG**. Authentication,
company routing/connectivity and inference remain **NOT-TESTED**. A VPN being on
does not establish a configured provider. Config was not modified to manufacture
a test, and no literal key, environment dump, Keychain or CLI auth/session file
was accessed. No provider request, retry, fallback or switching occurred.

After committing 918468e8, R5 used an absent physical temporary child path and
the installed canonical llmtool CLI:

```sh
LLMTOOL_HOME=<new-absent-absolute-path> tools/llmtool/.venv/bin/llmtool ask 'Reply with exactly: A11A3_COMPANY_LIVE_OK' --debug
```

Actual result: exit 1, empty stdout, code=CONFIG_PROVIDER_NOT_READY,
run_id=3a816d4dc6722a9c579a2d3f80710973, trace=not-saved,
diagnostics_persisted=false; no traceback; zero provider.request events.
The supplied diagnostics home did not exist afterward: **no-write assertion PASS**.
This validates config-failure CLI behavior, not company live inference. The requested
company live portion of R5 remains SKIPPED-CONFIG. User config metadata
(device/inode/size/mtime) was unchanged from preflight; no config writes were performed.
Additional installed-CLI checks on this exact SHA confirm both removed flags exit 2,
empty stdout and no run_id (argparse precedes run creation).

### Review, remaining gate and future integration note

review-change and validate-runtime workflows checked the actual remediation diff,
failure/output/privacy contract, Mac versus CI evidence and current milestone scope.
No additional material code finding remains in the requested remediation scope.
No runtime diagnostics artifacts were created in the repository. No packaged skill
changes were made by remediation; engineering skill changes come only from main's
authorized merge. Existing benchmark/reasoning artifacts remain untracked/untouched.
Whitespace, local document links/settings examples and workflow reconciliation pass.
Historical A11a.1/A11a.2 reports are unchanged.

Required company inference is still unavailable because a matching existing profile
is missing. Under WORKFLOW.md, missing required Mac checks remain merge-blocking;
SKIPPED-CONFIG is not a PASS. External re-review also remains outstanding. Source/
offline remediation is implemented, but **A11a.3 external-review remediation NOT
complete / MERGE BLOCKED** for the required live prerequisite. **A11a.4 NOT STARTED.**

A11a.4 integration requirement (documentation only):

> A11a.4 must establish the final persisted run_id before persisted events are emitted, or otherwise explicitly reconcile a store collision. Persisted DiagnosticEvent.run_id, RunSummary.run_id, user-visible run_id and store directory must remain identical.

Remediation changed cli.py, one store sort expression, three diagnostic test files,
llmtool README/ROADMAP and this report. Main integration separately added the listed
Cloud/local workflow files. No collision refactor or A11a.4 implementation was started.
