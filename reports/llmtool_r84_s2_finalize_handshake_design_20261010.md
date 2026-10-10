# PR #84 R84-S2 design v2 — finalize handshake (Claude, 2026-10-10)

> Mirror of [PR #84 comment](https://github.com/davidcatcat/davidcattoolsproject/pull/84#issuecomment-6096949550). Design only; no code changed. Awaiting coordinator and Security acceptance, and Codex's S1 commit.


**Owner instruction (2026-10-10):** the requirements owner asked Claude to fix R84-S2. Claude therefore **requests the S2 writer role**. Coding starts only after (a) Codex has committed and pushed S1, leaving the Mac tree clean (S1 is still uncommitted locally right now), and (b) the coordinator and Security reviewer accept this design. No files have been touched.

### Why v1 was wrong
The coordinator is right. In the R84-S2 race the supervisor may already have sent `MUTATION_STATE_UNKNOWN` and entered permanent retention, and the Host's cleanup would then `kill_group` it. "Kill, then drain to EOF" would destroy exactly the retention it has to honour. **Rule for v2: the Host never kills or reaps a supervisor that might be retaining.**

### Current behaviour (`5166a41`)
- **Supervisor** (`_git_supervisor.py:300–316`): when git exits it emits `TERMINAL/MUTATION_GIT_EXIT`, then loops on `git_inventory()`. Any control-pipe byte or signal leads to `_last_act` (`killpg(0)`, which includes itself). An `IdentityUnknown` leads to `TERMINAL/MUTATION_STATE_UNKNOWN` plus permanent retention.
- **Host** (`_git_add_host.py:139–153, 167–187`): on `GIT_EXIT` it **breaks**, then `kill_group` → `reap` → `post_state`. Any later UNKNOWN is never read.

### Design v2: positive finalization; anything else is UNKNOWN
1. **Supervisor, after `GIT_EXIT`:** keep monitoring as today. If a control frame arrives that is exactly `{domain, type:'FINALIZE', nonce}`:
   1. run one final `git_inventory(own, bound)`;
   2. if it passes, emit `FINALIZED`, then `_last_act(own)`. That is its own self-group kill, exactly as today; the Host does not kill it.
   3. If the inventory raises `IdentityUnknown`, emit UNKNOWN and retain, as today.

   Any other control byte keeps its current meaning (cancel → `_last_act`, with **no** `FINALIZED`). Before `GIT_EXIT`, behaviour is unchanged.
2. **Host, on `GIT_EXIT`:** do **not** break. Record `terminal`, send `FINALIZE`, and keep the existing loop running (signals, parent liveness, status reads). Success requires all of the following, in this order:
   1. `GIT_EXIT`;
   2. then `FINALIZED`, with no UNKNOWN anywhere in the stream;
   3. then status-pipe **EOF**;
   4. then the supervisor's `ExitNote` (its own last act);
   5. all within a bounded deadline.
3. **Every other outcome sets `escaped`/uncertain and goes to `_unknown`, with no kill and no reap:**
   - an UNKNOWN frame at any time, including after `FINALIZED`, which is impossible but checked anyway;
   - a supervisor exit without `FINALIZED`, or exit before EOF;
   - the deadline passing;
   - a malformed frame;
   - a Host signal or parent loss after `GIT_EXIT`.

   The existing condition `ready and not terminal` becomes `ready and not finalized`.
4. **Only after positive finalization** does the Host run its existing passive checks, `kill_group` on the already-exited group (a no-op, kept for the anchor protocol), reap, the absence check and `post_state`.

### Why nothing can be lost
- A supervisor that hits `IdentityUnknown` writes UNKNOWN and then retains **without exiting**. The Host therefore never sees EOF plus exit, and it is never killed. Either the Host reads the UNKNOWN or its deadline expires; both lead to UNKNOWN.
- `FINALIZED` is written only after a **passing** final inventory, and the very next statement is the supervisor's self-kill. Nothing can be emitted after it.
- Bytes written before the supervisor exits remain in the kernel pipe buffer until EOF. The Host consumes the whole stream to EOF, so no queued UNKNOWN can be skipped. This does not rely on a one-time drain.
- Residual: an anomaly that only arises **after** the supervisor's final inventory and self-kill (for example a descendant that escaped after git exited and is invisible to `PPID_ONLY`) is not detectable by this protocol. That is the already-recorded C3 detached-E2E `NOT_VERIFIED`, unchanged by v2.

### Scope and tests (on approval)
- **Product:** `_git_supervisor.py` (FINALIZE handling in the post-exit loop) and `_git_add_host.py` (success gate and terminal condition). No new authority, no resolver, no workflow or ai-loop change.
- **Tests:**
  - Euclid's composed reproducer (GIT_EXIT, then UNKNOWN on EPERM/ENOMEM) must **fail before** the fix and pass after it, with an assertion that no `kill_group`/`reap`/`post_state` happens;
  - FINALIZED-then-EOF success;
  - supervisor exit without FINALIZED → UNKNOWN with no reap;
  - deadline → UNKNOWN;
  - a malformed or duplicate FINALIZED;
  - signal or parent loss between GIT_EXIT and FINALIZED;
  - the existing native suites, 14/14 and 20+20, plus the F1 product-failure tests on the final bytes.
- **Attempt cap:** two repair attempts for this root cause, then a coordinator decision. The reviewer must be a non-author (not Claude).

你需要做：無
