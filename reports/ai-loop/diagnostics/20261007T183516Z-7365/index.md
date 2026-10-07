# AI-loop diagnostic handoff

Status: **STOPPED**. Evidence only; no containment/security approval, feature publication or automatic recovery.

- Run: `20261007T183516Z-7365`
- Task: `A11b.2.2.1-containment-f3-recovery-20261007`
- Candidate HEAD: `474679ecda36da610a96e7ec84da39a5dd3ae33b`
- Original control: `98b1871be51128da8dcdcf6eb32c27e2af89fa14`
- Source ZIP SHA-256: `a00a9141f083ed00ff3a3e5dfdb1d36016a9bda657e73f04f0e6478877b4d1e4`
- Source code and raw Codex/Claude/authentication logs remain on the Mac.
- [Projected runner summary](summary.json); [provenance](provenance.json); [capture hashes](source-hashes.json).

Native result: `hung_supervisor` / `FAIL`.
- `fd_inheritance`: `PASS`
- `sighup`: `PASS`
- `sigint`: `PASS`
- `sigterm`: `PASS`

## Published evidence files

- [mac-containment-1.json](mac-containment-1.json)
- [provenance.json](provenance.json)
- [source-hashes.json](source-hashes.json)
- [summary.json](summary.json)
- [tests-1-mandatory-final-0.stderr.log](tests-1-mandatory-final-0.stderr.log)
- [tests-1-mandatory-final-0.stdout.log](tests-1-mandatory-final-0.stdout.log)
- [validation.md](validation.md)
