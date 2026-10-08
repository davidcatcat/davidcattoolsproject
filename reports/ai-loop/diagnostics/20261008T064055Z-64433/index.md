# AI-loop diagnostic handoff

Status: **STOPPED**. Evidence only; no containment/security approval, feature publication or automatic recovery.

- Run: `20261008T064055Z-64433`
- Task: `A11b.2.2.1-containment-f3-recovery-20261007`
- Candidate HEAD: `59381d49a0f3992f1edd6ac5c8e1fd5a87d3cdad`
- Original control: `3c08dba54e6ac59db8385816ad51f2faaeacabee`
- Source ZIP SHA-256: `2d55260393c91be998d873c920da66216a543dfb722e2f3be10c7ecfc087e3fc`
- Source code and raw Codex/Claude/authentication logs remain on the Mac.
- [Projected runner summary](summary.json); [provenance](provenance.json); [capture hashes](source-hashes.json).

Native result: `kqueue_non_reaping` / `FAIL`.
- `fd_inheritance`: `PASS`
- `hung_supervisor`: `PASS`
- `identity_reuse`: `PASS`
- `leader_exit_descendant`: `PASS`
- `macos_start_time`: `PASS`
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
- [validation.md](validation.md)
