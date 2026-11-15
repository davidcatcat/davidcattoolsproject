# Mac CI durations: pure-test PRs vs code PRs (#1502 D2)

Data note for the Owner's D2 question on #1502: should pure test PRs keep their impact-scoped Mac job? Facts only. It lists options and makes no recommendation.

## Source and method

- Window: the 40 most recently merged PRs, #2022 to #2083 (merged 2026-11-14T23:36Z to 2026-11-15T06:35Z).
- Source: the `llmtool Mac CI` workflow runs for each PR's merged head SHA, read with `gh api` (`actions/runs?head_sha=`, then `runs/<id>/jobs`). Skipped runs and skipped jobs are excluded.
- Class by changed paths: **docs** (only documentation paths), **tests-only** (only `tests/` or `test_*` files), **code** (anything else).
- Job run time: `completed_at - started_at`. For a sharded run it is the longest shard plus the `check (macOS 3.13)` job. Queue wait: job `started_at - created_at`. Total runner time: the sum over all Mac jobs of that run, including `select`.
- Not measured: time from PR open to the `mac-ci` label, cancelled or superseded runs on earlier heads, and runner billing.

## Summary

| Class | PRs | Mac mode | Median job run time (s) | Range (s) | Median total runner time (s) | Sum of runner time (s) |
|---|---:|---|---:|---|---:|---:|
| tests-only | 19 | selected modules | 139 | 120-265 | 150 | 3027 |
| tests-only | 1 | sharded (3) | 1361 | - | 3784 | 3784 |
| code | 13 | selected modules | 1070 | 137-1254 | 1083 | 12284 |
| code | 4 | sharded (3) | 1460 | 1281-1700 | 3845 | 15400 |
| docs | 3 | not run | - | - | 0 | 0 |

- Queue wait before a Mac job started was 1-7 s (median 3 s) for every run in the window (GitHub-hosted `macos-15`; sharded runs also used `macos-15-intel`).
- Tests-only PRs used 6811 runner-seconds in total (20 PRs). Code PRs used 27684 (17 PRs).
- One tests-only PR (#2062, one new test module) got the full sharded suite. The selector's reason was not analyzed (UNKNOWN).

## Per PR

| PR | Class | Files | Mac mode | Job run time (s) | Longest queue wait (s) | Total runner time (s) |
|---:|---|---:|---|---:|---:|---:|
| #2083 | tests-only | 3 | selected modules | 265 | 3 | 274 |
| #2080 | tests-only | 2 | selected modules | 147 | 2 | 157 |
| #2077 | code | 3 | selected modules | 137 | 2 | 146 |
| #2076 | tests-only | 1 | selected modules | 190 | 2 | 203 |
| #2075 | tests-only | 3 | selected modules | 141 | 3 | 154 |
| #2072 | tests-only | 3 | selected modules | 125 | 2 | 137 |
| #2070 | tests-only | 1 | selected modules | 141 | 3 | 153 |
| #2069 | tests-only | 1 | selected modules | 120 | 2 | 131 |
| #2068 | tests-only | 1 | selected modules | 141 | 2 | 152 |
| #2067 | docs | 1 | not run | - | - | 0 |
| #2066 | tests-only | 1 | selected modules | 139 | 3 | 148 |
| #2065 | tests-only | 1 | selected modules | 136 | 3 | 149 |
| #2063 | tests-only | 1 | selected modules | 137 | 1 | 148 |
| #2062 | tests-only | 1 | sharded (3) | 1361 | 3 | 3784 |
| #2061 | docs | 6 | not run | - | - | 0 |
| #2060 | tests-only | 1 | selected modules | 136 | 3 | 146 |
| #2059 | tests-only | 1 | selected modules | 138 | 3 | 149 |
| #2058 | tests-only | 1 | selected modules | 152 | 4 | 164 |
| #2056 | tests-only | 1 | selected modules | 153 | 3 | 165 |
| #2055 | code | 3 | selected modules | 1028 | 2 | 1041 |
| #2053 | code | 2 | selected modules | 695 | 2 | 708 |
| #2052 | code | 6 | selected modules | 880 | 2 | 902 |
| #2050 | code | 4 | selected modules | 1230 | 3 | 1253 |
| #2048 | code | 17 | sharded (3) | 1532 | 5 | 4081 |
| #2045 | code | 11 | sharded (3) | 1389 | 7 | 3736 |
| #2043 | docs | 1 | not run | - | - | 0 |
| #2042 | tests-only | 1 | selected modules | 130 | 4 | 139 |
| #2040 | tests-only | 1 | selected modules | 143 | 3 | 160 |
| #2039 | code | 2 | selected modules | 1254 | 3 | 1269 |
| #2038 | code | 2 | selected modules | 1136 | 3 | 1145 |
| #2037 | code | 5 | selected modules | 211 | 2 | 229 |
| #2036 | code | 3 | selected modules | 1163 | 2 | 1175 |
| #2033 | code | 2 | sharded (3) | 1700 | 7 | 3955 |
| #2032 | code | 4 | selected modules | 1141 | 3 | 1173 |
| #2028 | code | 5 | selected modules | 1076 | 3 | 1087 |
| #2026 | code | 8 | selected modules | 1054 | 2 | 1073 |
| #2025 | tests-only | 1 | selected modules | 137 | 3 | 148 |
| #2024 | tests-only | 1 | selected modules | 138 | 2 | 150 |
| #2023 | code | 2 | selected modules | 1070 | 2 | 1083 |
| #2022 | code | 2 | sharded (3) | 1281 | 3 | 3628 |

## Options for an Owner ruling (not a recommendation)

1. Keep the current rule: pure test PRs keep their impact-scoped Mac job (about 2-3 runner-minutes each in this window).
2. Skip the Mac job for pure test PRs whose changed modules have no Mac-specific behaviour, and keep the Linux check.
3. Batch pure test PRs (the main PM already batches #1230 pins), so one Mac job covers several modules.
4. Look into why a single tests-only PR (#2062) selected the sharded suite before deciding.

Collected by sub-agent PM subpm-opencode-lane for main PM bf697d9e on 2026-11-15. Read-only GitHub API data; it does not show runner health or billing.
