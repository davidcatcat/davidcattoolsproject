# A11b.2.2.2 F1 initial runner handoff — 2026-10-09

Implementation status: **NEEDS_DECISION — C1 frame-size design boundary**.
Native Mac acceptance: **PENDING — not executed**.

Task: `A11b222-F1-PrivateGitADD-20261009`.
Runner-supplied frozen control revision: `0de90276c7efaceb41f36c478cbd4d0480ea833e`.
Verified local branch: `feat/a11b222-f1-private-git-add-20261009`.
Verified base/HEAD: `1f1fe91c4ecd48762c48fdd1e3b5f8c347673c5c`.
Verified root: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Remote identity from local Git configuration: `git@github.com:davidcatcat/davidcattoolsproject.git`.
Working tree was clean before this report. No remote synchronization is claimed.

## Blocking evidence

The [merged implementation contract](llmtool_a11b222_codex_private_fixture_implementation_contract_20261009.md)
§3 requires a single canonical JSON handshake bounded to 16 KiB and explicitly
requires reviewer reassessment if the complete canonical typed Action cannot fit.
§7 requires the maximum permitted legitimate path list to fit and says to fail
closed at the design boundary if it does not. The embedded runner task repeats
this requirement and directs `NEEDS_DECISION` for an unresolved frame-size limit.

The unchanged `action_contract.py` permits 32 files, paths up to 1,024 UTF-8
bytes, and canonical Action JSON up to 65,536 bytes. A pure, in-memory
serialization diagnostic using `/usr/bin/python3` exited 0 and established:

| Observation | Bytes/count |
| --- | ---: |
| Permitted path count used | 32 |
| Each ASCII path length | 767 |
| Compact JSON path list alone | 24,641 bytes |
| Complete canonical typed Action | 57,430 bytes |
| Existing Action JSON limit | 65,536 bytes |
| Required Host handshake limit | 16,384 bytes |

`parse_action(action.to_json())` successfully round-tripped the exact Action.
Thus even a permitted list below the individual path-length maximum exceeds
the entire handshake budget before origin, manifest, identity, preflight and
nonce fields. This diagnostic validates schema acceptance only; it is not
fixture-provenance, Git-preflight, mutation, channel-security or Mac-containment
evidence. No repository fixture or child process was created by the diagnostic.

Reproduce from the canonical root (pure contract data, no Git operations):

```sh
PYTHONPATH=tools/llmtool /usr/bin/python3 - <<'PY'
from llmtool import action_contract as c
import json
paths = tuple('/'.join(('a' * 180, 'b' * 180, 'c' * 180,
                       ('file%02d' % n) + 'd' * 218))
              for n in range(c.MAX_FILES))
metadata = c.FileMetadata(1, 1, 0o600, 501, 20, 1, 1, 1)
state = c.FileState(True, c.FileType.REGULAR, 'a' * 64, metadata)
files = tuple(c.FileExpectation(path, state) for path in paths)
action = c.Action(1, 'frame-boundary', 1, c.ActionKind.GIT_OPERATION,
    c.TaskBinding('frame-boundary', 'b' * 32, 1, 'c' * 64),
    c.RepositoryBinding('d' * 64, '/fixture', 1, 1, 'e' * 40), paths,
    c.GitAddPayload(c.GitOperation.ADD, files, 'f' * 64))
raw = action.to_json().encode('utf-8')
assert c.parse_action(raw.decode()).to_json() == raw.decode()
print(len(paths), len(paths[0].encode()),
      len(json.dumps(list(paths), separators=(',', ':')).encode()), len(raw))
PY
```

Expected diagnostic output: `32 767 24641 57430`.

## Disposition and scope

STOP before adding an executable private host. Coordinator-owned independent
design review must reconcile the bounded frame with the accepted typed Action
sizes, through an explicitly reviewed frame-limit, encoding or input-bound
revision. No bound was silently reduced, frame enlarged, extra channel added,
or source allowlist expanded. F2 and real user-repository ADD remain unauthorized.

Only this allowlisted initial report was added. No implementation/test files,
control files, scripts, launcher, Git index, commits, branches or remote refs
were changed by Codex. Required host/test files were intentionally not created
because the explicit design-boundary STOP precedes implementation.

## Runner-owned evidence gates

| Gate | Initial evidence status |
| --- | --- |
| Related syntax and unit checks | PENDING — outer runner has not verified |
| Both Python full suites | PENDING — outer runner has not verified |
| Private F1 channel/authority/containment tests | PENDING — implementation blocked |
| Native 14-scenario acceptance and 20+20 SIGKILL probes | PENDING — not executed |
| Independent ordinary Claude code review | PENDING — no implementation candidate |
| Separate specialist Security and Functional QA | PENDING |
| Exact-candidate CI and Draft PR publication | PENDING — runner-owned |

No QA, integrated execution, native containment, provider privacy, token savings,
or merge readiness PASS is claimed. The outer runner owns any subsequent
truthful, source-bound verification entries.
