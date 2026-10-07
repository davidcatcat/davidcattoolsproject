# AI-loop diagnostic handoff

Status: **STOPPED**. Evidence only; no containment/security approval, feature publication or automatic recovery.

- Run: `20261007T162747Z-96025`
- Task: `A11b.2.2.1-containment-f3-recovery-20261007`
- Candidate HEAD: `a00eda49aa0f0c3486d27589148361d67e7bdb3e`
- Original control: `8c96e7ad60fd5c39f13f0a563afcc6cc26f84753`
- Source ZIP SHA-256: `28ef582c74cc1f22a3f88e3c60c01c1531ea16306ad62f55df59ba6b17889629`
- Source code and raw Codex/Claude/authentication logs remain on the Mac.
- [Projected runner summary](summary.json); [provenance](provenance.json); [capture hashes](source-hashes.json).

Native result: `hung_supervisor` / `FAIL`.
- `fd_inheritance`: `PASS`
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
