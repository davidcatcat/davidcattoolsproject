# PR #84 R84-S2 fix — developer evidence (Claude, 2026-10-10)

> Mirror of the PR #84 comment. Developer evidence; a non-author review is required.


**Scope** (3 files, +304/−12):
- `_git_add_host.py`
- `_git_supervisor.py`
- `tests/test_git_add_authority.py`

No workflow, ai-loop, resolver or new authority. Base is `c4fe9cd` (S1 closed), unchanged on the remote at push time.

### Change (design v2, finalize handshake)
- **Host:**
  - `MUTATION_GIT_EXIT` is no longer final. The Host sends `FINALIZE` and settles **only** when it has seen, in order: `FINALIZED`, then status **EOF**, then the supervisor's own `ExitNote`, all within `_FINALIZE_SECONDS` (5).
  - A `TERMINAL/MUTATION_STATE_UNKNOWN` at **any** point (including after EXIT or FINALIZED) sets `escaped` and rejects.
  - Exit or EOF without FINALIZED is `SUPERVISOR_LOST`. A duplicate or out-of-order frame is rejected. The deadline gives TIMEOUT.
  - All of these satisfy `ready and not settled`, so the Host goes to `_unknown`: no `kill_group`, reap, `post_state` or handler restore.
  - `START_FAILED` (no Git spawned) is unchanged.
- **Supervisor:**
  - After EXIT it keeps monitoring.
  - Only an exact `{domain, type: FINALIZE, nonce}` triggers one more `git_inventory(own, bound)`, then `emit('FINALIZED')`, then the existing `_last_act`.
  - A failing inventory goes to the existing UNKNOWN retention.
  - Any other control byte, a partial frame or EOF is cancellation, as before, and emits no FINALIZED.

### Test-first evidence
- **Red on `c4fe9cd`:** the new `TerminalFinalizationTests` gave **FAILED (failures=8)**.
  - The independent composed reproduction (the real supervisor emitting READY → CHILD_IDENTITY → GIT_EXIT → UNKNOWN for EPERM and ENOMEM) was **SETTLED** by the real Host.
  - So were exit-without-FINALIZED, FINALIZED-then-UNKNOWN, duplicate FINALIZED, a second terminal and the deadline case.
- **Green on `b4f5223`:**
  - all of the above end in `STATE_UNKNOWN` with no kill_group/reap/post_state/killpg/kill/restore;
  - FINALIZED → EOF → exit settles as `MUTATION_GIT_EXIT` with `interrupted=False`, and FINALIZE is sent exactly once.
- **`SupervisorFinalizeTests`** (the real `_product_main`):
  - an exact FINALIZE gives READY, CHILD_IDENTITY, GIT_EXIT, FINALIZED, then last act;
  - CANCEL, garbage or a wrong-nonce FINALIZE cancels without FINALIZED;
  - a failing **final** inventory (the 2nd post-EXIT inventory) leaves the supervisor retained with UNKNOWN and no FINALIZED or last act. Retention is observed by trace, because the retention loop swallows exceptions.

### Mac validation on final bytes (macOS 15.7.9)
| Check | Result |
| --- | --- |
| `.venv` 3.13.3 `make check` | **1389 OK** (skipped=3) |
| `/usr/bin/python3` 3.9.6 | **1389 OK** (skipped=4) |
| Real chain: `test_git_add_private` (14) + `test_git_add_product_failures` (41) | OK. Real Git ADD still settles APPLIED through the handshake |
| Native acceptance | **14/14**, SIGKILL 20+20, max **0.013 s** |
| Leftover processes or fixtures | none / 0 |

Evidence sha256 (first 16): `_git_add_host.py` `33cc40164667eb29`, `_git_supervisor.py` `e0764a22fe67cb37`, test `1ba83d8a1ee43122`, native JSON `9cb883fc218e33bc` (Mac-private).

**Residual (unchanged):** an anomaly that only arises after the supervisor's final inventory and self-kill (detached escape) remains C3 E2E `NOT_VERIFIED`.

**Requested:**
- exact-head CI;
- a **non-author** Security and Functional review of the design and code. Claude is excluded; Lorentz and Euclid would fit. This is attempt 1 of 2 for this root cause.

你需要做：無
