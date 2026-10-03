# A11a.4 — Persisted ask integration validation — 2026-10-03

Status: **IMPLEMENTED ON FEATURE BRANCH / CORRECTION ROUND 1 LOCAL CANONICAL PASS /
INDEPENDENT RE-REVIEW AND FINAL-HEAD CI PENDING**. A11a.4 is not merged/DONE. Overall A11a remains
incomplete; A11a.5–7, A11b, A11c and A12 are NOT STARTED. No merge is authorized.
The current correction checkpoint at the end supersedes the explicitly historical
initial-candidate check/review sections; the prior permission event remains historical.

## Repository and exact implementation

- Environment: user-selected Codex Cloud; configured existing checkout only.
- Root/cwd: `/workspace/davidcattoolsproject`.
- Remote: `https://github.com/davidcatcat/davidcattoolsproject.git`.
- Branch: `feat/a11a4-persisted-ask-cloud-20261003`.
- Verified fetched base: `b9ac5389ea9e45fa3f66450e0269920ee03734e7`.
- Current corrected implementation commit: `7affd93dcc94f9026dc7f7330f1297af3044ab5e`.
- Initial implementation commit: `78c240eef32a4b2adb138595da57ed91e2a8cacf`;
  first frozen review candidate: `51aef5757df6d90fa2d99fa5dd809306f593e627`.
- Source/test manifest SHA-256:
  `a00ef5d3b685777d675fdb6470cd5d66304888c6f34c973ef547636d1dcbec1c`.
  Initial manifest was `e3adedaa58fd610140aba58426fdffc1f29e49b8975230eeb362e7723a553751`.
- Local main remains at `0d324038ac44ac9ca1c9b32a11fb9cc163e12a12`;
  it was not advanced or merged. The feature branch was created from the exact
  verified origin/main SHA, with initial ahead/behind 0/0 and a clean tree.
- Pre-review delivery commit adds only documentation to the implementation commit.
  Its exact SHA and clean-tree/frozen confirmation are supplied in the coordinator
  handoff; no report can include its own eventual Git commit SHA.

Manifest encoding: sort paths relative to `tools/llmtool`; hash each path UTF-8,
NUL, exact file bytes, NUL. Files: `llmtool/cli.py`,
`llmtool/diagnostics_runtime.py`, `llmtool/diagnostics_store.py`,
`tests/test_diagnostics_ask_persistence.py`, `tests/test_diagnostics_runtime.py`,
`tests/test_diagnostics_settings.py`, `tests/test_diagnostics_store.py`.
Documentation is excluded. Source/test bytes are committed and unchanged in the
documentation delivery. The working-tree test runs preceded the implementation
commit; their scope/provenance is distinguished below.

Read latest AGENTS.md, llmtool WORKFLOW.md, root/tool README, ROADMAP, A11a spec,
A11a.3 validation and inspect-repo, implement-feature, safe-git, review-change,
validate-runtime skills before implementation. No clone, worktree, alternate
checkout, Mac service/auth material or production provider invocation was used.

## Capability acknowledgment

| Role | Canonical agent ID | Requested model | Requested reasoning | Verification |
| --- | --- | --- | --- | --- |
| Developer | `/root/developer` | `gpt-6.1-sol` | high | Spawn accepted requested configuration; actual runtime model/reasoning unverified |
| Independent Reviewer | `/root/reviewer` | `gpt-6-astra` | high | Spawn accepted requested configuration; actual runtime model/reasoning unverified |

The tools expose no actual model/reasoning runtime metadata. Requested values are
not asserted as independently verified execution identities. The coordinator
keeps Developer and Reviewer work separate; this table does not assert a review
verdict. Independent same-session review and external/human review are separate.

## Implemented behavior and limits

Ask-only mutually exclusive `--save-diagnostics` / `--no-save-diagnostics` use
argparse None for unspecified. Explicit CLI selection overrides trusted user
diagnostics.persist_metadata; default remains false. Debug, verbose and
LLMTOOL_HOME alone do not enable writes. Project config cannot enable/change
diagnostics. Config show and configure preservation contracts are unchanged.

After ephemeral RunContext creation, legacy skill/input-source validation occurs
first. Trusted settings then resolve before config readiness or blocking input
reads. On an early expected input/skill error, settings resolution is best effort:
valid settings can still save that error; malformed settings cannot replace it.
Explicit save with unusable settings reports a secondary unavailable warning
without opening storage. An enabled store starts before provider/credential access. The existing
no-clobber collision resolver supplies the final ID; buffered immutable run.start
events are rebound to that ID before any persisted append. Subsequent event IDs
remain sequential, and event/summary/display/directory run IDs agree. Settings
malformation fails configuration before store/provider I/O. Valid settings allow
input/readiness/provider failures to save a completed error summary with zero
provider calls when the original flow fails before a request.

Accepted events stream through the existing strict store API. Completion builds a
typed version-1 RunSummary from accepted start/end timestamps, duration, event
count, dropped count, actual trace bytes, provider type and final task code.
Only successful complete publication with no store warning sets persisted=true.
Task failure metadata reports saved/unavailable/not-saved accurately. Normal
success remains quiet unless storage fails; verbose/debug use actual saved state.

Store constructor/start, append false/exception, completion false/exception,
publication/fsync/write/capacity/unsafe state and descriptor-close failures remain
secondary: preserve stdout, task exit and provider call count. Primary safe task
error then code/run ID precede at most one fixed bounded store warning. Warning
collection copies only a closed store code, never foreign message/repr/exception.
Interrupt remains 130 with status interrupted; broken pipe remains 0 with status
output_closed. The CLI finally block releases any remaining writer.

The backend change is limited to the start-failure and writer-close release loops:
an individual close exception no longer prevents remaining descriptors from being
attempted. There is no storage traversal, permissions, locking, capacity,
retention or publication redesign. A close whose OS outcome is unknown is not
retried; unknown partial artifacts remain subject to the existing conservative
policy. Summary remains the completion marker for individually published files.

No prompt, response, retrieved content, raw HTTP body/CLI stderr, credentials,
Authorization, reasoning, environment, arbitrary argv or host paths are collected
into persistence. Existing allowlisted metadata and strict safe scalar validation
are reused. No provider module/capability, A9/A10 flow or packaged skill changed.
No retry/fallback/executor, runs/trace/doctor or later milestone was implemented.

## Cloud platform and validation gates

Linux `6.18.44` x86_64 / glibc 2.41; Python 3.12.14; PyYAML 6.0.3;
setuptools 84.0.0. Installed executable:
`/workspace/davidcattoolsproject/tools/llmtool/.venv/bin/llmtool`, version 0.1.0.
Imported llmtool module resolves inside this configured checkout. The venv is
ignored package/test state, not another repository checkout.

Required gates were declared before implementation: focused public CLI/store
failure tests; diagnostics and affected CLI regressions; canonical compileall/full
offline suite; package build/provenance and installed-CLI local mock HTTP smoke;
docs/privacy/scope review and diff check; exact final-head GitHub Python 3.9/3.14
canonical and clean-tree CI; independent review and required external/human review.

Mac/company VPN, existing Claude/Codex login sessions, paid public-provider live
inference and macOS storage are **NOT-TESTED / optional for this slice**. The diff
changes ask observation/storage wiring, not provider transports/auth/isolation or
an actual Mac dependency. Mock success and real Linux storage do not prove live
inference, Mac behavior or historical provider health. No prior A11a.3 live gate
or PASS was imported as a new A11a.4 result. Unsupported backend behavior is
simulated; no Windows backend exists.

## Historical initial-candidate checks and evidence

All commands launched from the configured repository root. Test-generated
temporary fixtures and empty provider runtime directories are not checkouts.

```sh
python3 -m venv --system-site-packages tools/llmtool/.venv
tools/llmtool/.venv/bin/python -m pip install --no-deps --no-build-isolation -e tools/llmtool
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_diagnostics*.py' -q
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p 'test_cli.py' -q
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
tools/llmtool/.venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/llmtool-a11a4-wheel tools/llmtool
tools/llmtool/.venv/bin/python /tmp/llmtool-a11a4-installed-smoke.py
git diff --check
```

| Check | Outcome / evidence |
| --- | --- |
| Final focused diagnostics | PASS, 174/174, 0 failure/error/skip, 0.790 s, exit 0; includes 28 new public persisted-ask tests |
| Existing ask-runtime regression | PASS, 47/47, 0.289 s, exit 0 after real flag/help changes |
| Affected CLI regression | PASS, 31/31, 7.923 s, exit 0; granted loopback network access for the existing local mock server |
| Canonical first attempt | FAIL / environment prerequisite, 780 discovered, 778 pass, 1 failure + 1 error, no skips, 15.902 s, make exit 2; two inherited Codex fixture cases cannot create isolated runtime |
| Canonical technical rerun before final test addition | Technical PASS, 780/780, no skip, 15.694 s, exit 0; per-command `/var/tmp` write allowance used before obtaining the user's separate prospective permission; authorization gap retained below |
| Final 781-test local canonical revision | NOT-TESTED / pending prospective filesystem permission; production unchanged from 780-test rerun, but one new warning-privacy test was subsequently added |
| Editable installation | PASS in repository venv; installed module/executable identify checkout; initial system/user install failed on read-only `/home/agent/.local/lib` and was replaced by local venv |
| Wheel build/provenance | PASS, exit 0; changed production module bytes match checkout; packaged analyze-log skill present; wheel SHA-256 `d4ddadb48c271f58475fdbf34b7c266f349fb45e0127752850ad03b3d7508f61` |
| Installed CLI local mock HTTP smoke | PASS, 11 cases / 10 HTTP requests; each ready invocation exactly one request; config readiness failure zero requests |
| Docs/local links/manifest/diff self-review | PASS before documentation delivery; no material self-review finding remains; independent review pending |
| Final exact-head remote CI | NOT-TESTED / pending authorized publication; historical base CI is not candidate CI |

Ordinary self-test fixes before external review: replace historical flag rejection
and parser-field absence with real mutually exclusive/help/default checks; explicitly
chmod intended unsafe fixtures (Cloud umask 0077 otherwise makes them private);
accept the existing symlink backend classification as unavailable while asserting
safe containment. No security assertion was weakened. Close-release tests drive
the small cleanup fix. Warning privacy tests prove foreign messages/private
objects never enter warning output. These are not post-review correction rounds.

### Canonical environment and permission provenance

The environment supplies a read-only `/tmp/.git`. The unchanged codex-cli isolation
correctly refuses `/tmp` when a test's temporary mocked cwd has that repository
boundary; fallback `/var/tmp` is read-only in the default filesystem profile.
The two affected inherited tests are
`test_four_transports_real_resolution_and_normalization` and
`test_ask_dispatch_stdout_and_failure_contract_for_all_profiles` (local-codex
subcase). Observed error: safe "Codex CLI isolated working directory is not
available"; temporary-directory creation diagnosis was errno=30.

The technical rerun used exec_command sandbox_permissions=with_additional_permissions,
additional_permissions.network.enabled=true and
additional_permissions.file_system.write=["/var/tmp"] with the exact canonical
make command above. It completed before the coordinator's stop instruction about
the user's reserved permission decision arrived. It is **not claimed as approved
or retroactively approved**. The coordinator disclosed the mistake and requested
prospective permission. No further `/var/tmp` writes occurred while it remained
pending. Read-only inspection afterward found zero remaining
`/var/tmp/llmtool-codex-*` directories. `/tmp/.git` and system directories were
never deleted, moved or changed; provider isolation/tests were not bypassed.
Log `/tmp/llmtool-a11a4-canonical-py312.log` records the technical rerun.

An explicitly authorized final local rerun or exact final-head GitHub Actions
canonical check remains required evidence for the final 781-test revision. This
report does not substitute the prior 780-test result for that gate.

### Installed CLI smoke matrix

The temporary helper uses a real local ThreadingHTTPServer, isolated temporary
user config/HOME and real Linux POSIX storage, preserving inherited proxy/CA
settings. No existing user config or credentials are touched. Each success stdout
is exactly `A11A4_MOCK_HTTP_OK` plus newline; errors have empty stdout. Safe stderr
and all persisted bytes exclude synthetic prompt/output/raw-body/path markers.

| Case | Exit | HTTP requests | Completed metadata |
| --- | ---: | ---: | --- |
| Default / debug | 0 each | 1 each | absent; home remains absent |
| CLI save normal / verbose / debug | 0 each | 1 each | success |
| Trusted user save | 0 | 1 | success |
| Trusted user save + CLI no-save | 0 | 1 | absent |
| Provider HTTP error + save | 1 | 1 | error, trace=saved |
| Unsafe store + provider success | 0 | 1 | unavailable; one bounded warning |
| Unsafe store + provider HTTP error | 1 | 1 | unavailable; task line/code precede warning |
| Config readiness failure + save | 1 | 0 | error; no stdin/provider access |

Safe result capture: `/tmp/llmtool-a11a4-installed-smoke.json`.
This is **local mock HTTP + real storage**, not live provider inference.

## Historical initial frozen review/publication checkpoint

PR #18 is verified MERGED at 2026-10-03T18:07:25Z, base SHA above.
[Base CI 37143028827](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37143028827)
is SUCCESS for that exact base, not for this implementation. Current root/tool
README and ROADMAP now reflect A11a.3 merged and A11a.4 feature work. The old
A11a.3 report receives an additive current merge checkpoint; original Mac/live/
remediation evidence remains historical. A11a.1/2 reports and spec are unchanged.

Developer review-change/validate-runtime self-review is complete. Independent
Reviewer exact-diff review: **PENDING**. Explicit external/human review:
**PENDING**; a same-session agent verdict alone does not satisfy it. Post-review
correction rounds used: **0** at freeze; maximum **3** before escalation.
Publication/PR/remote-head CI: **PENDING**. Final local canonical permission and
781-test rerun: **PENDING**. Merge readiness remains blocked by unsatisfied gates
and the user's no-merge instruction; milestone DONE is not asserted.

The Developer stops all tracked writes after the documentation delivery commit
and frozen-candidate handoff. Review uses that exact committed SHA; any correction
requires coordinator authorization, new affected checks and re-review. Source is
not pushed and no PR is opened until the coordinator authorizes publication.

## Correction round 1 — current local validation and second-review checkpoint

Initial independent review of frozen candidate
`51aef5757df6d90fa2d99fa5dd809306f593e627`: **REQUEST CHANGES** with one P2
blocker R1. The initial CLI resolved diagnostics config before legacy skill/input
validation even with persistence off. Reviewer baseline/candidate replay showed
malformed user YAML plus `ask x --file x` changed the original input-conflict
first line / USER_INPUT_ERROR to YAML / CONFIG_INVALID; invalid skill had the same
precedence regression. R1 is accepted and corrected; independent closure remains
pending second review. No additional material finding was reported on the other
mechanisms or descriptor-close loops, and those loops create no Mac prerequisite.

The coordinator-provided initial review validation summary is retained separately
from the new source evidence:

> diagnostics174 PASS; CLI31 PASS with network allowance; full diff/docs/diffcheckPASS;
> local781 canonical NT pending previous /var/tmp permission; external/human separatepending.

Corrected source/test commit: `7affd93dcc94f9026dc7f7330f1297af3044ab5e`.
Current seven-file manifest:
`a00ef5d3b685777d675fdb6470cd5d66304888c6f34c973ef547636d1dcbec1c`.
Tests ran on the exact bytes subsequently committed; no production/test bytes
changed after those tests. The subsequent delivery commit changes README/report
only. Initial 780 and pending 781 results above are historical, not final evidence.

The correction first performs the original skill/input-source checks. When an
expected early error exists, resolving diagnostics settings is secondary: valid
settings still permit metadata persistence of that original error, while unusable
settings cannot mask it. Default/no-save keep the original first line/code without
a store warning. Explicit save with unusable settings emits one fixed unavailable
warning after the original USER_INPUT_ERROR line/code and performs no store I/O.
On a valid input path, diagnostics config errors still fail before store/provider
access. Provider readiness still precedes blocking input reads.

Three new matrix tests cover input conflict, invalid `invalid/skill`, combined
skill/input conflict (skill retains precedence), malformed YAML, invalid trusted
user opt-in retention, default/no-save/save and valid trusted user opt-in. All early
failure cases assert exit 1, empty stdout, original safe first line / USER_INPUT_ERROR,
zero credential/provider calls, and correct saved/unavailable/not-saved state.

The user explicitly granted prospective test permission:
"允許" and "允許測試暫時寫入 /var/tmp". This applies to Developer/Reviewer tests
and existing empty ephemeral Codex runtime fixtures only. The earlier permission
event remains an authorization gap; this grant is not retroactive approval.
The new canonical run uses the exact make command shown above with
exec_command sandbox_permissions=with_additional_permissions,
additional_permissions.network.enabled=true and
additional_permissions.file_system.write=["/var/tmp"]. No provider isolation or
test is weakened, and `/tmp/.git`/system directories remain unchanged. Read-only
inspection after this approved run finds zero matching runtime directories.

| Corrected implementation check | Result |
| --- | --- |
| Diagnostics, including 31 new persisted-ask tests | PASS, 177/177, 0 failure/error/skip, 0.897 s, exit 0 |
| Affected skill validation | PASS, 18/18, 0 failure/error/skip, 0.067 s, exit 0 |
| Affected CLI regression | PASS, 31/31, 0 failure/error/skip, 7.466 s, exit 0, network allowance |
| Approved canonical compileall/full suite | PASS, 784/784, 0 failure/error/skip, 15.493 s, exit 0 |
| Rebuilt wheel/source/resource verification | PASS, exit 0; SHA-256 `e8f256fc121dbe005bfac2afd26e6295aa8043aea44ae73561c61afa2fe0536d` |
| Installed CLI local mock HTTP/real POSIX smoke rerun | PASS, same 11 cases / 10 one-per-ready-invocation requests; readiness failure zero; not live inference |
| Documentation links, manifest and diff self-review | PASS; full correction diff checked before delivery |

Current logs/results:
`/tmp/llmtool-a11a4-canonical-approved-round1-py312.log` and
`/tmp/llmtool-a11a4-installed-smoke-round1.json`. The installed editable CLI remains
0.1.0 and imports this exact Cloud checkout. Build/smoke rerun commands are the
same as the earlier list; the smoke JSON has its round1 filename.

Post-review correction rounds used: **1 of 3**. Independent second review:
**PENDING**. Required external/human review: **PENDING**. Exact final publication-head
Python 3.9/3.14 CI and authorized publication/PR: **PENDING**. The local canonical
permission/check prerequisite is now satisfied prospectively for this revision.
No Mac/company/Claude/paid-provider live PASS is claimed. A11a.4 remains implemented
on a feature branch, not merged/DONE; overall A11a and later-slice status is unchanged.
The Developer freezes all writes after the correction documentation delivery and
hands the exact clean candidate SHA to the coordinator for second review.
