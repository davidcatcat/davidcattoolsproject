# Reports

Dated review, validation and measurement reports. A report is a readable
summary of evidence, not the evidence store itself.

## What belongs here

- The decision or finding, with severity and disposition.
- Exact SHAs, commands, exit codes, test counts, environment and limits.
- Small machine-readable results a later step consumes (for example
  `results.json`, preregistration, gold answers) and the benchmark inputs
  needed to reproduce a published claim.

## What does not

- Raw logs, full test transcripts, terminal captures and large fixtures that
  nothing re-reads. Keep them as GitHub Actions artifacts or private evidence,
  and record the path or artifact name plus a SHA-256 digest in the report.
- Absolute home paths, usernames, tokens or other private data. The repository
  is public.
- Status that belongs in the [llmtool Roadmap](../tools/llmtool/ROADMAP.md).

## Archived artifacts

Files removed from the tree stay in Git history. Each affected report lists the
removed file, its SHA-256 and Git blob, and the `git show <sha>:<path>` command
to retrieve it. See [pr49_security_remediation_20261008](pr49_security_remediation_20261008/README.md).
