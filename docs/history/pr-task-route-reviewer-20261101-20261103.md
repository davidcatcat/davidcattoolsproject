# PR, Task, author route and Reviewer, 2026-11-01 to 2026-11-03

This table answers #1208 step 3a (from audit #1174). It lists every PR merged from 2026-11-01 to 2026-11-03 UTC, one section per merge day.

- **Built:** 2026-11-14, read-only, by sub-agent PMs subpm-gwold4 and subpm-gwold5.
- **Sources:** the merged PR list, PR head branch names, PM `developer_dispatch` markers on Issues (matched by branch), Task IDs named in those markers or PR descriptions, local TaskStore `published_pr` records, and review verdict activity markers in PR comments.
- **Kept:** only PR numbers, merge dates, Task ID prefixes, route names and Reviewer names.
- **`UNKNOWN`:** no record was found. It is not a finding about the PR.
- **Author route:** from the PM dispatch marker for the PR branch, else from the route word in the branch name. Where neither exists it is `UNKNOWN`; no route is guessed from PR titles or text. It was not re-checked against provider logs.
- **Reviewer:** the actor of the last PASS verdict marker on the PR. Where that actor is the same family as the author route, the cell lists every PASS actor on the PR in order, and a note follows the table.

## Merged 2026-11-01

39 PRs. Author route `UNKNOWN`: 34 PRs.

| PR | Merged (UTC) | Task (prefix) | Author route | Route source | Reviewer |
| --- | --- | --- | --- | --- | --- |
| #703 | 2026-11-01 | df224a23 | UNKNOWN | none | Claude |
| #718 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #719 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #720 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #722 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #723 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #724 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #726 | 2026-11-01 | 6e5035ec, d4111dcb | UNKNOWN | none | Claude |
| #727 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #728 | 2026-11-01 | UNKNOWN | Claude | branch name | Codex |
| #729 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #730 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #731 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #732 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #733 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #734 | 2026-11-01 | UNKNOWN | OpenCode | branch name | Codex |
| #735 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #736 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #737 | 2026-11-01 | UNKNOWN | AGY | branch name | Claude |
| #738 | 2026-11-01 | UNKNOWN | company-gw | branch name | Codex |
| #739 | 2026-11-01 | f8711927 | UNKNOWN | none | Codex |
| #740 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #741 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #742 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #743 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #744 | 2026-11-01 | UNKNOWN | OpenCode | branch name | Claude |
| #745 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #746 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #747 | 2026-11-01 | 43ec01ae | UNKNOWN | none | Codex |
| #749 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #750 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #751 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #752 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #753 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Codex |
| #754 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #755 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #756 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #758 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
| #759 | 2026-11-01 | UNKNOWN | UNKNOWN | none | Claude |
