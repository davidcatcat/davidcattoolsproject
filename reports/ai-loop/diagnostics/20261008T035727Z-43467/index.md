# AI-loop diagnostic handoff

Status: **STOPPED**. Evidence only; no containment/security approval, feature publication or automatic recovery.

- Run: `20261008T035727Z-43467`
- Task: `A11b.2.2.1-containment-f3-recovery-20261007`
- Candidate HEAD: `0dc05b75844f9caa4c4ccf9c28c8b21972fd25f9`
- Original control: `a831946fc75823e2a97296a4d5f33bc375f3dc8e`
- Source ZIP SHA-256: `081afc24ed32486862fdf6476c0d719c1cc8abd69afbfaaf863ffb0bdfc5cfbe`
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
