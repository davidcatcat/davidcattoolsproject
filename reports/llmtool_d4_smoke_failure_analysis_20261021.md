# D4 one-call smoke failure — root-cause analysis (2026-10-21)

**Requested by:** Owner (「你分析一下, 上傳報告」).
**Author:** Claude (independent Reviewer). Read-only analysis of the retained private evidence and Host/Worker source at canonical `3f5dae94`.
**Scope:** the single D4 smoke attempt, job digest `f9b1c7e3…`. Durable receipt: `UNKNOWN / OPENCODE_RUNTIME_INVALID` (#120 6295012525, corrected in 6295069067).
No credential content was read and no OpenCode/provider process was started for this analysis.

## 1. Conclusion

**The attempt failed in pre-launch runtime validation, before any OpenCode process existed.** The most strongly supported cause is that the job pinned the executable as the nvm **symlink** `~/.nvm/versions/node/v24.20.0/bin/opencode`, not the real binary `…/lib/node_modules/@opencode/cli/bin/opencode.exe`. The Worker's `_file_hash()` uses `lstat()` and requires a regular file, so a symlink is rejected with `OPENCODE_RUNTIME_INVALID` (`opencode_worker.py:205`).

From this:

| Item | Status |
|---|---|
| OpenCode server launched | **No** |
| D8a credential read / handoff (`prepare_server`) | **No**: never reached |
| OpenCode client started | **No** |
| Provider requests | **0** |
| Token usage | **0** (no inference) |
| Process cleanup | Nothing to clean: no process was spawned |

The PM's correction (marking these UNKNOWN) was the right call given only the receipt. The **physical evidence below** resolves them.

## 2. Evidence

1. **The isolated state root was never used.** The retained `opencode-009cb122…` contains only llmtool's own `config/service.json`; `data/`, `home/`, `state/`, `tmp/` and `cache/` are **empty**. All directories carry mtime **23:46:03** and were never modified afterwards (deleting a created file would change the directory mtime). The receipt was written at **23:46:04**, one second later.
2. **A real OpenCode always writes on start.** Claude's offline end-to-end run of the same pinned OpenCode 2.0.32 with the Worker's own `provision_runtime` / `configuration` / `_environment` (including `OPENCODE_DB=:memory:`), against a local fake provider, created `data/opencode/log/opencode.log` and `tmp/` files on every `serve` start. An empty state root plus a one-second job therefore means `serve` never ran.
3. **The post-client `RUNTIME_INVALID` path is not reachable here.** `_server_json` raises `RUNTIME_INVALID` only for URL, route or port shape. It uses the **same URL** that had already passed the stricter readiness check, with fixed routes plus a percent-encoded session id. If readiness passed, that path cannot fail with this code. And readiness itself happens only after `serve` starts, which (1) and (2) rule out.
4. **Direct reproduction with the same source (`3f5dae94`) and the same binary:**
   - `w._file_hash(Path('~/.nvm/versions/node/v24.20.0/bin/opencode'))` → **`OPENCODE_RUNTIME_INVALID`**
   - `w._file_hash(Path('…/@opencode/cli/bin/opencode.exe'))` → `fe7a74cbb62b…`, which matches the pinned SHA-256
5. **The PM's recorded runtime path is the symlink.** The earlier D4 report (#120 6283001714) lists the executable as `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/opencode` with SHA-256 `fe7a74cb…`. `shasum` follows symlinks, so the hash matched while the path was a link: `ls -l` shows `bin/opencode -> ../lib/node_modules/@opencode/cli/bin/opencode.exe`.

**Limit:** the job's `RuntimePin.executable` value itself was not persisted in the retained evidence, so (5) supports the symlink diagnosis without recording the field directly. The other pre-launch `RUNTIME_INVALID` checks were examined against the retained facts: state-root `.git` ancestor (none), 0700 modes (all 0700) and state inside the task root (no). They do not fit, and the hash would match the real binary. **Whichever pre-launch check failed, the conclusions in §1 hold**, because of evidence (1) and (2).

## 3. Fix and prevention

| # | Action | Owner | Size |
|---|---|---|---|
| 1 | **Re-run the smoke with a fresh admission that pins the resolved real path** `…/@opencode/cli/bin/opencode.exe`. No code change is required. Under D8 this needs no Owner approval. | PM | trivial |
| 2 | In the runner/Host builder, resolve the executable once (`os.path.realpath`) and pin **that** path and hash, so a symlinked install can never be pinned again. | Developer | small |
| 3 | **Persist the failure stage** in the receipt (`PRELAUNCH` / `READINESS` / `CREDENTIAL` / `CLIENT` / `EVIDENCE` / `CLEANUP`), and split the overloaded code (e.g. `OPENCODE_EXECUTABLE_INVALID`, `OPENCODE_STATE_INVALID`, `OPENCODE_READINESS_INVALID`). Then a failure never again leaves launch, credential and request counts unknown. | Developer | small–medium |
| 4 | Run the deterministic pre-launch validation (`_validate`) as a **dry run before consuming the one-use admission/reservation**, so a configuration error does not burn a live attempt. | Developer | small |
| 5 | Keep the retained evidence until the re-run passes; then retire per D6. | PM | — |

## 4. About closing OpenCode

Before the smoke, Claude advised the Owner to close the interactive OpenCode TUI so the native credential DB would be quiescent during the D8a read. The records show the TUI (PID 88513) exited after that. The background OpenCode service (PID 44509) was also gone in the PM's later checks; the retained evidence does not show who stopped it. **Rule for future runs:** the PM must not stop user-started OpenCode sessions or services. If they block a run, the PM asks the Owner or waits. Task-owned Workers are separate private processes and are the only ones the Host stops.

## 5. Status

D4 smoke: **FAIL (configuration, pre-launch)**, with **zero inference and no credential read**. Next step: action #1. On success, proceed to the first real OpenCode Developer package as already recorded (#120 6289682796).
