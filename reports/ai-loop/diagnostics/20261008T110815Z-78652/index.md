# AI-loop diagnostic handoff

Status: **STOPPED**. Evidence only; no containment/security approval, feature publication or automatic recovery.

- Run: `20261008T110815Z-78652`
- Task: `A11b.2.2.1-containment-f3-recovery-20261007`
- Candidate HEAD: `12cabadaf42559a6c48a3aa707925de021f2e2e7`
- Original control: `59d3ce80ec7f2718782099374f2a2eb65d3dddcc`
- Source ZIP SHA-256: `5c61baa55672cd669af28c52466e14d6777e450055fcfd12ae066c2738a2590e`
- Source code and raw Codex/Claude/authentication logs remain on the Mac.
- [Projected runner summary](summary.json); [provenance](provenance.json); [capture hashes](source-hashes.json).

Native result: `complete` / `no failure recorded`.
- `code_runtime_binding`: `PASS`
- `fd_inheritance`: `PASS`
- `hung_supervisor`: `PASS`
- `identity_reuse`: `PASS`
- `kqueue_non_reaping`: `PASS`
- `leader_exit_descendant`: `PASS`
- `macos_start_time`: `PASS`
- `parent_sigkill_plain`: `PASS`
- `parent_sigkill_popen`: `PASS`
- `protocol_framing`: `PASS`
- `public_import_boundary`: `PASS`
- `sighup`: `PASS`
- `sigint`: `PASS`
- `sigterm`: `PASS`

## Published evidence files

- [mac-containment-0.json](mac-containment-0.json)
- [provenance.json](provenance.json)
- [source-hashes.json](source-hashes.json)
- [summary.json](summary.json)
- [tests-0-mandatory-final-0.stderr.log](tests-0-mandatory-final-0.stderr.log)
- [tests-0-mandatory-final-0.stdout.log](tests-0-mandatory-final-0.stdout.log)
- [triage-public.json](triage-public.json)
- [validation.md](validation.md)
