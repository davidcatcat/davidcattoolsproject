# D4 one-call smoke failure — root-cause analysis (2026-10-21)

**Requested by:** Owner (「你分析一下, 上傳報告」).
**Author:** Claude (independent Reviewer). Read-only analysis of the retained private evidence and Host/Worker source at canonical `3f5dae94`.
**Scope:** the single D4 smoke attempt, job digest `f9b1c7e3…`. Durable receipt: `UNKNOWN / OPENCODE_RUNTIME_INVALID` (#120 6295012525, corrected in 6295069067).
No credential content was read and no OpenCode/provider process was started for this analysis.

## 1. Conclusion

The retained receipt says `UNKNOWN / OPENCODE_RUNTIME_INVALID`. The strongest current root-cause hypothesis is that the job pinned the nvm **symlink** `~/.nvm/versions/node/v24.20.0/bin/opencode`, not the real binary `…/lib/node_modules/@opencode/cli/bin/opencode.exe`; the Worker's `_file_hash()` uses `lstat()` and requires a regular file, so this path would be rejected (`opencode_worker.py:205`). This is an inference from separate path/hash reproduction and the PM's path record: the original `RuntimePin.executable` and failure stage were not persisted.

From this:

| Item | Status |
|---|---|
| OpenCode server launched | **UNKNOWN** |
| D8a credential read / handoff (`prepare_server`) | **UNKNOWN** |
| OpenCode client started | **UNKNOWN** |
| Provider request count | **UNKNOWN** |
| Token usage | **UNKNOWN** |
| At-run process cleanup | **UNKNOWN** |

The PM's correction to retain these fields as UNKNOWN remains required. The evidence below supports the symlink hypothesis but cannot reconstruct the original stage or call counters.

## 2. Evidence

1. **Observed retained state-root contents.** The retained `opencode-009cb122…` contains llmtool's `config/service.json`; `data/`, `home/`, `state/`, `tmp/` and `cache/` were empty when inspected. This is consistent with no `serve` activity, but it does not prove the original process/credential/request history or exclude all cleanup.
2. **Separate reproduction.** Claude's offline end-to-end run of OpenCode 2.0.32 with the Worker runtime and a local fake provider wrote `data/opencode/log/opencode.log` and `tmp/` files on each observed `serve` start. That supports the hypothesis but does not recover what occurred in the original job.
3. `_server_json` raises `RUNTIME_INVALID` for URL, route or port shape; the separate reproduction makes an executable-path rejection plausible. The missing original stage prevents excluding other pre-launch checks.
4. **Direct reproduction with the same source (`3f5dae94`) and the same binary:**
   - `w._file_hash(Path('~/.nvm/versions/node/v24.20.0/bin/opencode'))` → **`OPENCODE_RUNTIME_INVALID`**
   - `w._file_hash(Path('…/@opencode/cli/bin/opencode.exe'))` → `fe7a74cbb62b…`, which matches the pinned SHA-256
5. **The PM's recorded runtime path is the symlink.** The earlier D4 report (#120 6283001714) lists the executable as `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/opencode` with SHA-256 `fe7a74cb…`. `shasum` follows symlinks, so the hash matched while the path was a link: `ls -l` shows `bin/opencode -> ../lib/node_modules/@opencode/cli/bin/opencode.exe`.

**Limit:** the original `RuntimePin.executable` and failure stage are absent. The PM's recorded symlink path plus separate `_file_hash()` reproduction make the symlink explanation likely, but it remains an inference. Original launch, credential handoff, request count, usage and cleanup stay UNKNOWN.

## 3. Fix and prevention

| # | Action | Owner | Size |
|---|---|---|---|
| 1 | Preserve both consumed smoke records; do not replay either Job. D4 remains background while direct Developer work proceeds. Any future separately admitted run must use the verified real executable path and fresh authorization. | PM | — |
| 2 | In the runner/Host builder, resolve the executable once (`os.path.realpath`) and pin **that** path and hash, so a symlinked install can never be pinned again. | Developer | small |
| 3 | **Persist the failure stage** in the receipt (`PRELAUNCH` / `READINESS` / `CREDENTIAL` / `CLIENT` / `EVIDENCE` / `CLEANUP`), and split the overloaded code (e.g. `OPENCODE_EXECUTABLE_INVALID`, `OPENCODE_STATE_INVALID`, `OPENCODE_READINESS_INVALID`). Then a failure never again leaves launch, credential and request counts unknown. | Developer | small–medium |
| 4 | Run the deterministic pre-launch validation (`_validate`) as a **dry run before consuming the one-use admission/reservation**, so a configuration error does not burn a live attempt. | Developer | small |
| 5 | Keep the retained evidence until the re-run passes; then retire per D6. | PM | — |

## 4. About closing OpenCode

Before the smoke, Claude advised the Owner to close the interactive OpenCode TUI so the native credential DB would be quiescent during the D8a read. The records show the TUI (PID 88513) exited after that. The background OpenCode service (PID 44509) was also gone in the PM's later checks; the retained evidence does not show who stopped it. **Rule for future runs:** the PM must not stop user-started OpenCode sessions or services. If they block a run, the PM asks the Owner or waits. Task-owned Workers are separate private processes and are the only ones the Host stops.

## 5. Status

D4 attempt one: terminal receipt `UNKNOWN / OPENCODE_RUNTIME_INVALID`; launch, credential handoff, request count, usage and cleanup are UNKNOWN. The symlink root cause is a supported inference only. Attempt two separately returned `OPENCODE_AUTH_REQUIRED`; neither attempt produced a verified model reply. Both records remain preserved and are not replayed. D4 is background; direct Developer work proceeds independently.
