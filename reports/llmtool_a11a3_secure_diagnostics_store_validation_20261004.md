# A11a.3 — User settings and secure POSIX store validation — 2026-10-04

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
