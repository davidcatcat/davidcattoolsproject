# GitHub Actions seven-day usage baseline — 2026-10-17

**OBSERVED metadata baseline; financial savings UNKNOWN; decision support only.**
Scope: `davidcatcat/davidcattoolsproject`, Issue [#183](https://github.com/davidcatcat/davidcattoolsproject/issues/183), parent [#99](https://github.com/davidcatcat/davidcattoolsproject/issues/99), known item #137 R12. No workflow/configuration changes, reruns, manual dispatch, billing requests, paid exports or settings changes were performed. Formal Integrator review is pending.

## Window and provenance

- Fixed observation cutoff: **2026-10-17T01:24:27Z**; exclusive upper bound.
- Seven-day lower bound: **2026-10-10T01:24:27Z**; inclusive. Exactly **604800 seconds**.
- Explicit local conversion, Asia/Taipei (UTC+08:00): **2026-10-10 09:24:27 through 2026-10-17 09:24:27**, upper exclusive. These are a rolling seven days, not seven complete local calendar days Oct11–17; the remaining Oct17 hours had not occurred at observation.
- Collection finished **2026-10-17T01:24:57Z**. Metadata reflects API responses gathered during these30 seconds; cutoff filters run creation, not a historical snapshot of every status at that instant. All collected runs were completed; all valid job intervals end before cutoff.
- Configured Cloud checkout `/workspace/davidcattoolsproject`; verified origin `https://github.com/davidcatcat/davidcattoolsproject.git`; fresh main **`cd7ee6494938df469ce7c9fc3f8b8948d9354ed9`**. Branch `report/codex-issue183-actions-usage-baseline`. Only this new report is changed. Existing open PRs #173/#174 and M2 source are untouched.
- Actual tools: `gh`2.46.0 and read-only repository Actions Runs/Jobs REST endpoints. HTTP success was required; no permission escalation or credential/environment dump. All actor/author/email/header/token/commit-message fields were excluded from the retained allowlist projection.
- Projection SHA-256: `9d2305e8b4f9a55ca216c11cd12e91b19f1771e0dda863502b0fcb3156167f30`. The private Cloud projection is not claimed to be a durable attachment; the run/job ledgers below retain the measurement fields so review does not depend on that private file.

The run-created cohort is **135 unique runs**, **138 attempts**, **268 unique job IDs**. Run pagination returned100+35 rows, both pages reported total_count135. All138 attempt-specific job endpoints were paginated; each had one page of0,1 or2 jobs, matching that endpoint's total_count. Three cancelled runs had zero jobs; that is an observed zero, not missing permission. One run had four attempts; all attempts1–4 were queried, not only the latest.

**Coverage limit:** runs created before the lower bound were not searched for reruns occurring during this window. Thus this is a reproducible run-created cohort baseline, not an exhaustive billing-period activity ledger. A complete all-activity accounting would also enumerate older-created runs with in-window attempts and obtain official usage data. The run endpoint's `updated_at` is an update time, not a completion timestamp; no completion time is invented from it. Jobs provide actual reported start/completion timestamps. Run completion timestamp remains **UNKNOWN** from the retained Runs fields; maximum latest-attempt job completion is at most a derived job bound, not the workflow's official finish.

## Reproducible collection and pagination

Commands were executed from the verified Cloud checkout; all API calls below are GETs. No workflow run was launched for this report.

```sh
gh --version
gh api 'repos/davidcatcat/davidcattoolsproject/actions/runs?per_page=100&created=2026-10-10T01%3A24%3A27Z..2026-10-17T01%3A24%3A27Z' --paginate
# For every retained run, query each integer ATTEMPT from1 through run_attempt:
gh api 'repos/davidcatcat/davidcattoolsproject/actions/runs/RUN_ID/attempts/ATTEMPT/jobs?per_page=100' --paginate
```

The installed CLI does not support `--slurp`. An initial unsupported-flag invocation failed before HTTP; it was corrected once to supported `--paginate` plus Python `JSONDecoder.raw_decode` over concatenated page objects. No API failure was retried blindly. Collector calls had90s process deadlines, at most4 concurrent job reads, and a500-attempt cap (actual138).

Collection algorithm: concatenate all `workflow_runs` pages; require unique run IDs; retain `start <= created_at < cutoff`; enumerate all attempts; concatenate every jobs page; require unique job IDs per response and across the collection; verify page-length sums equal reported totals. Retain only columns represented in the ledgers (plus status and public run/job URLs). URLs are reconstructed from IDs below. A subsequent rerun/deletion/branch advancement may change API responses, so frozen IDs, SHAs, timestamps and observed attempt counts are retained here.

## Observed counts and job elapsed

Elapsed is `completed_at - started_at` for each valid job timestamp pair, summed across parallel jobs and all selected attempts. It excludes queue time and does not equal workflow elapsed, billed/rounded minutes or charges. Seconds are summed before display rounding. All267 valid job intervals fall wholly inside the window, so clipping these selected jobs to the window does not change their sum.

| Workflow / event | Runs | Attempts | Jobs | Known job wall-minutes | Jobs with UNKNOWN elapsed |
| --- | ---: | ---: | ---: | ---: | ---: |
| llmtool CI / push | 56 | 56 | 106 | 322.08 | 0 |
| llmtool CI / pull_request | 75 | 75 | 149 | 458.60 | 0 |
| llmtool CI / workflow_dispatch | 1 | 1 | 1 | 3.48 | 0 |
| ai-loop helper CI / push | 1 | 1 | 2 | 0.52 | 0 |
| ai-loop helper CI / pull_request | 2 | 5 | 10 | 1.43 | 1 |
| **Total** | **135** | **138** | **268** | **786.12 known subtotal** | **1** |

- Latest run conclusions:122 success,8 failure,5 cancelled. These are latest-attempt run outcomes, not attempt-level outcomes.
- Job conclusions across attempts:248 success,17 failure,3 cancelled. No conclusion is normalized to PASS. Completed/skipped/cancelled/absent checks are distinct.
- Runner labels: all268 jobs report `ubuntu-latest` (Linux label); observed macOS0 and Windows0 in this cohort. Labels do not prove billing SKU or actual image/architecture. `nlf CI`:0 runs; its required platforms were **NOT_OBSERVED**, not passed or waived.
- First-attempt jobs262; rerun jobs6 from one ai-loop run. Five valid rerun jobs sum56s (0.93 minutes); the sixth elapsed is UNKNOWN. Overall full job-wall total is **UNKNOWN**;786.12 is the valid267-job subtotal, not a substituted zero for the invalid row.
- UTC-created-date counts: Oct10=35, Oct11=19, Oct12=18, Oct13=10, Oct14=21, Oct15=17, Oct16=12, Oct17=3. First/last dates are partial days by design; daily workload is not controlled.

### Timestamp anomaly — retained, not repaired or rounded away

[Run38073072812 attempt3, job114283781796](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38073072812/job/114283781796), `helper (3.14)`, reports start `2026-10-10T18:33:52Z` but completion `2026-10-10T17:46:45Z` (negative2827s). Elapsed is **UNKNOWN**. No absolute value, negative subtraction or silent zero is used in a total. Both raw timestamps remain in the ledger. Attempt4's3.9 job reports an interval matching an earlier job; the API-reported distinct job ID is retained, without asserting independently billed execution. Attempt-specific metadata can contain rerun/carry-forward artifacts; official usage remains necessary.

## Dedup analysis: candidates, source identity and main

Grouping key is **(workflow path, head_branch, API head_sha)** across push and pull_request. This conservatively yields **8 candidate groups**, each one push plus one PR run:16 runs,32 jobs. The push-side16 jobs consumed **2456 observed seconds =40.93 wall-minutes**. This is historical candidate workload, **not** proven avoidable billed time or achieved savings. All eight candidates predate the PR#100 reference commit timestamp below. No candidate includes a normal main post-merge run.

The PR API `pull_requests[]` projection is populated for only5/77 PR runs:4 match the run's API head SHA,1 differs,72 are absent. No missing source identity is invented. Therefore these8 groups confirm matching *API identities*, but do not prove identical checked-out/tested revisions or equivalent integration checks. Historical merge refs and source heads can differ. Dedup suggestions must verify event-time source/base and workflow behavior before any future cancellation policy.

| Branch | API head SHA | Push run | PR run | Push-side wall-minutes |
| --- | --- | ---: | ---: | ---: |
| `feat/a11b222-f1-private-git-add-20261010-r3` | `fca30d8c529cb018a290f391ef1bcd968a579b9d` | [38034723327](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38034723327) | [38034725730](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38034725730) | 4.87 |
| `feat/a11b222-f1-private-git-add-20261010-r3` | `64aa0f4c09356d0c0adf085dbcceb7434e506896` | [38036173975](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38036173975) | [38036176798](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38036176798) | 4.65 |
| `feat/a11b222-f1-private-git-add-20261010-r3` | `5166a417a1bf7a448ee1c167e4ced669acaa5b20` | [38038624603](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38038624603) | [38038627671](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38038627671) | 5.43 |
| `feat/a11b222-f1-private-git-add-20261010-r3` | `c4fe9cdf05fb10bc7d115cfef5f0f4820244f7bf` | [38048295596](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38048295596) | [38048297938](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38048297938) | 5.25 |
| `feat/a11b222-f1-private-git-add-20261010-r3` | `b4f52235ea1e69ad4ff426d31b09ae9b576f744c` | [38050791882](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38050791882) | [38050792488](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38050792488) | 5.22 |
| `feat/a11b222-f1-private-git-add-20261010-r3` | `82ce1206dad04f6c9ed39ece00231bad259ee994` | [38055456483](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38055456483) | [38055461497](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38055461497) | 5.17 |
| `docs/a11-f1-delivery-bug-triage-20261011` | `96d49577c52c4e5059595fe1503b7a3e04a31b42` | [38069373419](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38069373419) | [38069442367](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38069442367) | 5.35 |
| `docs/a11-f1-delivery-bug-triage-20261011` | `ef9d75077f79e466f41d1c811a2500b89307b49b` | [38069813409](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38069813409) | [38069818376](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38069818376) | 5.00 |

Concrete source-identity caution: [run38974313605](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38974313605) reports API head `5905976438a556af75d9039102e3488b66c43e13`, whereas its PR#174 snapshot reports source head `113eeec5a582c10d9fc5e5e4faf4a4dbf3edc907` and base `cd7ee6494938df469ce7c9fc3f8b8948d9354ed9`. This metadata does not establish whether the API head is a merge ref or an earlier source revision, nor actual checkout; a PR association can advance. Logs were not fetched for this measurement. Do not merge these identities or treat the metadata discrepancy as a failed/passed exact-source gate.

There are **44 main push runs** (all llmtool), and13 non-main pushes (12 llmtool,1 ai-loop). A main merge is a distinct integration/source SHA with a post-merge check, not a removable duplicate of a feature PR. No grouping across differing branch/SHA is used.

### PR#100 context, not a seven-day savings experiment

Main reference commit `2cd337e082a3b152d862e16346b9e191a5570d25` has Git committer time `2026-10-11T04:04:52+08:00` = `2026-10-10T20:04:52Z`. This explains the repository docs' local Oct11 date. This is a **commit-time reference**, not a separately measured deployment timestamp. Cutoff is only6 days5h19m35s after that reference, so this cohort is **not seven complete post-change days** and cannot fulfill the stronger post-rollout savings experiment. No causal before/after or normalized-dollar comparison with the14-day historic report is claimed.

Within this window, llmtool counts before/after that reference are push14/42 and PR11/64 (one dispatch before). All8 matching-API candidate groups occur before it; none afterward. This is consistent with the current trigger design, but differing workloads, pre-change coverage and incomplete post-change duration prevent an achieved percentage claim.

## Gates that remain required

At pinned main, [llmtool-ci.yml](https://github.com/davidcatcat/davidcattoolsproject/blob/cd7ee6494938df469ce7c9fc3f8b8948d9354ed9/.github/workflows/llmtool-ci.yml) permits main push, path-filtered PR, and manual dispatch. It keeps **separate Python3.9 and3.14 jobs**, explicitly checks out and verifies PR source HEAD and event-time base ancestry, verifies job-owned interpreter safety/hash failure paths, runs full canonical checks and checks tree cleanliness. It cancels superseded PR runs, not main pushes. These requirements must remain intact.

Observed llmtool jobs:127 `check (3.9)` +127 `check (3.14)` plus2 historical single `check` jobs =256 jobs across132 llmtool runs. Of these runs,127 have the two named version jobs,2 have a single job,3 cancelled runs have no jobs. Names alone do not prove tests or exact source validation executed successfully; no claim that every historical run met current gates. Current explicit verification does not retroactively identify historical checkout SHAs.

At pinned main, [nlf-ci.yml](https://github.com/davidcatcat/davidcattoolsproject/blob/cd7ee6494938df469ce7c9fc3f8b8948d9354ed9/.github/workflows/nlf-ci.yml) has Ubuntu/macOS matrix plus separate Windows job, paths-filtered PR, main/feat push and dispatch. Platform-native smoke, Linux/macOS race checks and Windows-specific shell checks are distinct required evidence. Zero observed nlf runs is no reason to delete those platforms.

`ai-loop helper CI` contributes3 runs/6 attempts/12 Linux-labelled jobs. Its workflow path appears in run metadata but is absent from pinned main; active control-branch configuration and executable-tip acceptance were not inspected/changed by this task. Keep existing control-branch, dual-Python, independent security and Mac promotion gates; no cost recommendation authorizes skipping them.

## Financial precision

| Quantity | Status | Evidence needed for a different claim |
| --- | --- | --- |
| Selected run / attempt / job counts | OBSERVED135 /138 /268 | Runs + attempt-specific Jobs API, pagination checked |
| Valid job elapsed | OBSERVED47167s /786.12 minutes subtotal |267 valid timestamps;1 invalid job elapsed UNKNOWN |
| Complete cohort wall elapsed | UNKNOWN | Resolve anomalous job timing; account for rerun/carry-forward artifacts |
| Billing chargeable/rounded minutes by OS/SKU | UNKNOWN | Authorized official usage export/API with SKU, timestamps and charging rules |
| Included quota / credits used or remaining | UNKNOWN | Authorized billing-period quota/credit ledger |
| Current budget / utilization | UNKNOWN | Authorized current account/repo budget and usage evidence |
| Actual charged USD / net charges | UNKNOWN | Official charge/credit/invoice report matched to the same scope |
| Seven-day savings / percentage / net dollars | UNKNOWN | Comparable full post-rollout interval, baseline workload normalization and official charges |
| Candidate push-side workload | OBSERVED2456s /40.93 wall-minutes | Historical candidate rows; no conversion to billed minutes or money |

No per-OS rate, rounding model, quota exhaustion or net USD is inferred. Historic docs cite14-day estimates and an earlier budget; these are not evidence of current charges or realized savings. No billing access was requested, attempted or assumed. The wall subtotal is not a complete billable-activity denominator.

## Conservative decision matrix (no authorization to change CI)

| Family | Measured evidence | Recommendation for a future separately reviewed change | Gates retained |
| --- | --- | --- | --- |
| llmtool feature push + PR |8 historical matching-API groups,40.93 push-side wall-minutes, all before reference | Keep current PR-only feature coverage; first verify checkout/base equivalence if proposing further dedup. No additional trigger change justified by this window. | Exact source/base + full3.9/3.14, main post-merge, negative tests, Mac gates |
| llmtool main push |44 runs, distinct branch/SHA role | Retain; never classify post-merge checks as duplicate PRs merely by equivalent trees. | Main integration checks |
| Superseded PR / rerun |5 latest cancelled runs;1 ai-loop run with3 additional attempts and timing anomaly | If later failures cluster, investigate specific root causes; no automatic rerun or broad concurrency change recommended from these counts. | Exact candidate status; red never replaced by absence of CI |
| nlf triggers/platforms |0 observed runs, no measured duplicates | No reduction justified; measure a representative nlf interval before proposing configuration changes. | Linux/macOS/Windows and existing native/race/shell checks |
| ai-loop control |3 runs/12 jobs, 1 elapsed anomaly | No optimization/promotion inferred; a separate scoped design/review/native gate is needed for any controller change. | Executable-tip, independent review, dual-Python and required Mac acceptance |
| Reporting/billing |7-day created-cohort observed; incomplete post-change duration, no billing | Future authorized measurement can cover at least7 complete post-change days plus older-created in-window reruns and official usage. No owner setup prompt or new spend requested here. | Financial precision; no invented savings |

## Validation and handoff

- Red-first regression: **N/A**, report-only measurement; no product/test/workflow modifications.
- Data validation: exact7-day bounds;135 unique run IDs;138 distinct run/attempt pairs;268 unique jobs; every page total reconciled;267 nonnegative timestamp pairs; one anomaly retained UNKNOWN; integer elapsed subtotal47167s; candidate group IDs and elapsed checked against frozen projection. No silent missing timing or API permissions represented as zeros.
- Markdown validation: balanced fences/table widths, local relative-link checks where used, exact allowlist, `git diff --check`, public GitHub target spelling and obvious token/header/email/private-path scan. Author checks and read-only same-session Reviewer pre-review are advisory; formal non-author review remains pending.
- Runtime unit/full tests and native acceptance: **N/A for this report**. CI absence on a report-only path is not PASS. No workflow dispatch/rerun requested. Mac runtime functionality is **NOT_TESTED**; this report grants no Mac gate for any code PR.
- Follow-up for Integrator: verify actual report diff/HEAD, review measurement limits and separate cost metrics. PR#100 savings experiment remains incomplete, not closed by this created-cohort report. No merge authorization or controller/main write is implied.

## Appendix A — frozen run ledger

Workflow keys: L=`llmtool CI` / `.github/workflows/llmtool-ci.yml`; A=`ai-loop helper CI` / `.github/workflows/ai-loop-ci.yml`. Events remain literal. Timestamps UTC. `API head` is not claimed to be actual checked-out HEAD. `PR snapshot` is `number:source-head:base` (multiple associations separated by semicolons); UNKNOWN means absent. `latest started` is Runs API run_started_at and can refer to a later rerun; `updated` is not completion. Run URLs: `https://github.com/davidcatcat/davidcattoolsproject/actions/runs/<ID>`.

| Run ID | WF | Event | Branch | API head SHA | PR snapshot | Created | Latest started | Updated (not completed) | Status / conclusion | Attempts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| 38034723327 | L | push | feat/a11b222-f1-private-git-add-20261010-r3 | fca30d8c529cb018a290f391ef1bcd968a579b9d | UNKNOWN | 2026-10-10T07:31:37Z | 2026-10-10T07:31:37Z | 2026-10-10T07:34:14Z | completed/failure | 1 |
| 38034725730 | L | pull_request | feat/a11b222-f1-private-git-add-20261010-r3 | fca30d8c529cb018a290f391ef1bcd968a579b9d | UNKNOWN | 2026-10-10T07:31:39Z | 2026-10-10T07:31:39Z | 2026-10-10T07:33:55Z | completed/failure | 1 |
| 38036173975 | L | push | feat/a11b222-f1-private-git-add-20261010-r3 | 64aa0f4c09356d0c0adf085dbcceb7434e506896 | UNKNOWN | 2026-10-10T07:56:51Z | 2026-10-10T07:56:51Z | 2026-10-10T07:59:17Z | completed/success | 1 |
| 38036176798 | L | pull_request | feat/a11b222-f1-private-git-add-20261010-r3 | 64aa0f4c09356d0c0adf085dbcceb7434e506896 | UNKNOWN | 2026-10-10T07:56:55Z | 2026-10-10T07:56:55Z | 2026-10-10T08:00:05Z | completed/success | 1 |
| 38038624603 | L | push | feat/a11b222-f1-private-git-add-20261010-r3 | 5166a417a1bf7a448ee1c167e4ced669acaa5b20 | UNKNOWN | 2026-10-10T08:39:14Z | 2026-10-10T08:39:14Z | 2026-10-10T08:42:11Z | completed/success | 1 |
| 38038627671 | L | pull_request | feat/a11b222-f1-private-git-add-20261010-r3 | 5166a417a1bf7a448ee1c167e4ced669acaa5b20 | UNKNOWN | 2026-10-10T08:39:17Z | 2026-10-10T08:39:17Z | 2026-10-10T08:42:25Z | completed/success | 1 |
| 38048295596 | L | push | feat/a11b222-f1-private-git-add-20261010-r3 | c4fe9cdf05fb10bc7d115cfef5f0f4820244f7bf | UNKNOWN | 2026-10-10T11:24:26Z | 2026-10-10T11:24:26Z | 2026-10-10T11:27:31Z | completed/success | 1 |
| 38048297938 | L | pull_request | feat/a11b222-f1-private-git-add-20261010-r3 | c4fe9cdf05fb10bc7d115cfef5f0f4820244f7bf | UNKNOWN | 2026-10-10T11:24:29Z | 2026-10-10T11:24:29Z | 2026-10-10T11:27:04Z | completed/success | 1 |
| 38050791882 | L | push | feat/a11b222-f1-private-git-add-20261010-r3 | b4f52235ea1e69ad4ff426d31b09ae9b576f744c | UNKNOWN | 2026-10-10T12:06:59Z | 2026-10-10T12:06:59Z | 2026-10-10T12:09:48Z | completed/success | 1 |
| 38050792488 | L | pull_request | feat/a11b222-f1-private-git-add-20261010-r3 | b4f52235ea1e69ad4ff426d31b09ae9b576f744c | UNKNOWN | 2026-10-10T12:06:59Z | 2026-10-10T12:06:59Z | 2026-10-10T12:09:55Z | completed/success | 1 |
| 38055456483 | L | push | feat/a11b222-f1-private-git-add-20261010-r3 | 82ce1206dad04f6c9ed39ece00231bad259ee994 | UNKNOWN | 2026-10-10T13:22:19Z | 2026-10-10T13:22:19Z | 2026-10-10T13:25:09Z | completed/success | 1 |
| 38055461497 | L | pull_request | feat/a11b222-f1-private-git-add-20261010-r3 | 82ce1206dad04f6c9ed39ece00231bad259ee994 | UNKNOWN | 2026-10-10T13:22:24Z | 2026-10-10T13:22:24Z | 2026-10-10T13:24:54Z | completed/success | 1 |
| 38068367007 | L | push | main | 46b17b13d2d656247ffd7243ae935996f8f0a482 | UNKNOWN | 2026-10-10T16:36:42Z | 2026-10-10T16:36:42Z | 2026-10-10T16:39:37Z | completed/success | 1 |
| 38069175751 | L | push | docs/a11-f1-delivery-bug-triage-20261011 | 0851dda194aa4bd032ffd84329d1b6843db69214 | UNKNOWN | 2026-10-10T16:48:58Z | 2026-10-10T16:48:58Z | 2026-10-10T16:51:51Z | completed/success | 1 |
| 38069201639 | L | push | docs/a11-f1-delivery-bug-triage-20261011 | eb6dfe6996dc6dc1527aa5278f5167e4d6a5dc6f | UNKNOWN | 2026-10-10T16:49:22Z | 2026-10-10T16:49:22Z | 2026-10-10T16:50:07Z | completed/cancelled | 1 |
| 38069250144 | L | push | docs/a11-f1-delivery-bug-triage-20261011 | 9484e0d60ac5972fe364b958a2af6f8e1835ed53 | UNKNOWN | 2026-10-10T16:50:06Z | 2026-10-10T16:50:06Z | 2026-10-10T16:51:46Z | completed/cancelled | 1 |
| 38069366690 | L | push | docs/a11-f1-delivery-bug-triage-20261011 | 4c4d51da4ec859a3450ce13ce32049ade2d48399 | UNKNOWN | 2026-10-10T16:51:45Z | 2026-10-10T16:51:45Z | 2026-10-10T16:51:51Z | completed/cancelled | 1 |
| 38069373419 | L | push | docs/a11-f1-delivery-bug-triage-20261011 | 96d49577c52c4e5059595fe1503b7a3e04a31b42 | UNKNOWN | 2026-10-10T16:51:50Z | 2026-10-10T16:51:50Z | 2026-10-10T16:54:44Z | completed/success | 1 |
| 38069442367 | L | pull_request | docs/a11-f1-delivery-bug-triage-20261011 | 96d49577c52c4e5059595fe1503b7a3e04a31b42 | UNKNOWN | 2026-10-10T16:52:48Z | 2026-10-10T16:52:48Z | 2026-10-10T16:55:43Z | completed/success | 1 |
| 38069799930 | L | pull_request | docs/a11-f1-delivery-bug-triage-20261011 | ce97f95d09af85743f7e456881dfc9c312c6a1ff | UNKNOWN | 2026-10-10T16:58:05Z | 2026-10-10T16:58:05Z | 2026-10-10T16:58:38Z | completed/cancelled | 1 |
| 38069813409 | L | push | docs/a11-f1-delivery-bug-triage-20261011 | ef9d75077f79e466f41d1c811a2500b89307b49b | UNKNOWN | 2026-10-10T16:58:16Z | 2026-10-10T16:58:16Z | 2026-10-10T17:01:10Z | completed/success | 1 |
| 38069818376 | L | pull_request | docs/a11-f1-delivery-bug-triage-20261011 | ef9d75077f79e466f41d1c811a2500b89307b49b | UNKNOWN | 2026-10-10T16:58:21Z | 2026-10-10T16:58:21Z | 2026-10-10T17:01:25Z | completed/success | 1 |
| 38070628943 | L | push | main | c441071cd40abd8b6e916889ef10de64d4fa6c99 | UNKNOWN | 2026-10-10T17:10:07Z | 2026-10-10T17:10:07Z | 2026-10-10T17:12:52Z | completed/success | 1 |
| 38073072812 | A | pull_request | automation/stage-a11b3-d1-20261011 | ff16b20abb04a965fdf0724ce7e53516e04b5832 | UNKNOWN | 2026-10-10T17:45:59Z | 2026-10-10T19:01:12Z | 2026-10-10T19:01:28Z | completed/success | 4 |
| 38078206890 | A | push | automation/ai-loop | ff16b20abb04a965fdf0724ce7e53516e04b5832 | UNKNOWN | 2026-10-10T19:02:30Z | 2026-10-10T19:02:30Z | 2026-10-10T19:02:56Z | completed/success | 1 |
| 38078689450 | L | pull_request | chore/ci-cost-llmtool-p1 | 1caa366d18bb7fd990a5de345af5403728b19030 | UNKNOWN | 2026-10-10T19:09:42Z | 2026-10-10T19:09:42Z | 2026-10-10T19:15:01Z | completed/success | 1 |
| 38079142443 | L | workflow_dispatch | chore/ci-cost-llmtool-p1 | 1caa366d18bb7fd990a5de345af5403728b19030 | UNKNOWN | 2026-10-10T19:16:27Z | 2026-10-10T19:16:27Z | 2026-10-10T19:20:00Z | completed/failure | 1 |
| 38081677441 | L | pull_request | chore/ci-cost-llmtool-p1 | 0adbc2c590eaff25efa671dd57b0bc8c1305725c | UNKNOWN | 2026-10-10T19:54:58Z | 2026-10-10T19:54:58Z | 2026-10-10T19:57:35Z | completed/success | 1 |
| 38082328745 | L | push | main | 2cd337e082a3b152d862e16346b9e191a5570d25 | UNKNOWN | 2026-10-10T20:04:55Z | 2026-10-10T20:04:55Z | 2026-10-10T20:07:34Z | completed/success | 1 |
| 38082823896 | A | pull_request | automation/stage-d1-repin-after-pr100-20261011 | 477472180bf1628366a60ff101f4213f96844b9f | UNKNOWN | 2026-10-10T20:12:22Z | 2026-10-10T20:12:22Z | 2026-10-10T20:12:41Z | completed/success | 1 |
| 38087138863 | L | pull_request | feat/a11b3-d1-offline-authorization-20261011 | 6f98033ce9e9fddc10a385f152eb9ef2942a1b4e | UNKNOWN | 2026-10-10T21:18:57Z | 2026-10-10T21:18:57Z | 2026-10-10T21:22:17Z | completed/success | 1 |
| 38088365926 | L | pull_request | feat/a11b5-v1-offline-verification-budget-20261011 | a9452c2115fbde8710e644ad475b433753262c78 | UNKNOWN | 2026-10-10T21:38:00Z | 2026-10-10T21:38:00Z | 2026-10-10T21:40:52Z | completed/success | 1 |
| 38089623011 | L | pull_request | test/a11b6-cli-spawn-permission-redaction-20261011 | 1b4501111859fca9589c53e1089466b02cfdc190 | UNKNOWN | 2026-10-10T21:58:02Z | 2026-10-10T21:58:02Z | 2026-10-10T22:00:54Z | completed/success | 1 |
| 38091109467 | L | push | main | 7aae8925d0e9f067e589c3735556f5d1a6cb6c18 | UNKNOWN | 2026-10-10T22:21:22Z | 2026-10-10T22:21:22Z | 2026-10-10T22:24:11Z | completed/success | 1 |
| 38092274997 | L | pull_request | feat/a11b3-d1-offline-authorization-20261011 | 488a21d83dfb05dec1296ee4a8a3e95213f7e604 | UNKNOWN | 2026-10-10T22:40:04Z | 2026-10-10T22:40:04Z | 2026-10-10T22:43:01Z | completed/success | 1 |
| 38112341344 | L | push | main | 96526be86e0af46608dee52a674c0c6fc4183f0b | UNKNOWN | 2026-10-11T04:40:43Z | 2026-10-11T04:40:43Z | 2026-10-11T04:43:40Z | completed/success | 1 |
| 38113627544 | L | pull_request | feat/a11b5-v1-offline-verification-budget-20261011 | 32863c66e4b403c7a475f8f42797c6aa24cafe3d | UNKNOWN | 2026-10-11T05:04:48Z | 2026-10-11T05:04:48Z | 2026-10-11T05:07:43Z | completed/success | 1 |
| 38114610903 | L | push | main | e8acf68b3f2a2471609019963fbb17edd178e359 | UNKNOWN | 2026-10-11T05:23:10Z | 2026-10-11T05:23:10Z | 2026-10-11T05:25:51Z | completed/success | 1 |
| 38115959118 | L | pull_request | feat/a11b4-p1-offline-patch-preflight-20261011 | 0440fd0678c93df90ced49f103a10bdec24975ce | UNKNOWN | 2026-10-11T05:48:00Z | 2026-10-11T05:48:00Z | 2026-10-11T05:50:59Z | completed/success | 1 |
| 38117034771 | L | pull_request | test/a11b6-http-error-redaction-20261011 | 6594d5217784b43d482666d4e0bd6a3f2871f2f9 | UNKNOWN | 2026-10-11T06:07:34Z | 2026-10-11T06:07:34Z | 2026-10-11T06:10:58Z | completed/success | 1 |
| 38117407887 | L | push | main | 90f4099e0af54ea5f2d2844a793475dfed5172bc | UNKNOWN | 2026-10-11T06:14:22Z | 2026-10-11T06:14:22Z | 2026-10-11T06:17:13Z | completed/success | 1 |
| 38117619198 | L | push | main | d18911c53eee57ef446ba0182f9a6a6eddc6fb20 | UNKNOWN | 2026-10-11T06:18:13Z | 2026-10-11T06:18:13Z | 2026-10-11T06:21:07Z | completed/success | 1 |
| 38118418148 | L | pull_request | test/a11b4-p1-nfc-path-collision-regression-20261011 | f8b6913641e3a31d3d61641ca4f405a43c9427ce | UNKNOWN | 2026-10-11T06:32:54Z | 2026-10-11T06:32:54Z | 2026-10-11T06:33:04Z | completed/failure | 1 |
| 38118512840 | L | pull_request | test/a11b4-p1-nfc-path-collision-regression-20261011 | 5c8bec1f20b957a45caad5f53f75e449aca69bd3 | UNKNOWN | 2026-10-11T06:34:39Z | 2026-10-11T06:34:39Z | 2026-10-11T06:37:29Z | completed/success | 1 |
| 38119486310 | L | push | main | 371e49f79592e698ff18220a5cb1175a880014c5 | UNKNOWN | 2026-10-11T06:52:41Z | 2026-10-11T06:52:41Z | 2026-10-11T06:55:23Z | completed/success | 1 |
| 38119684577 | L | pull_request | docs/ai-team-two-fixed-worktrees-policy-20261011 | 04a52be45c333c6f54a6a38a5b94bf52a96361f7 | UNKNOWN | 2026-10-11T06:56:16Z | 2026-10-11T06:56:16Z | 2026-10-11T06:59:06Z | completed/success | 1 |
| 38120752289 | L | push | main | d5982c495f58a21d109ee70deda98b8061bccdee | UNKNOWN | 2026-10-11T07:15:25Z | 2026-10-11T07:15:25Z | 2026-10-11T07:18:34Z | completed/success | 1 |
| 38120919391 | L | pull_request | docs/ai-team-pilot-activation-gates-20261011 | cfb37d0dafa2e94243771d22307f105f5aafe876 | UNKNOWN | 2026-10-11T07:18:25Z | 2026-10-11T07:18:25Z | 2026-10-11T07:21:08Z | completed/success | 1 |
| 38121336976 | L | push | main | 5c75a9f935cac075bd76374635a6c06c53e3316d | UNKNOWN | 2026-10-11T07:25:49Z | 2026-10-11T07:25:49Z | 2026-10-11T07:28:46Z | completed/success | 1 |
| 38153108040 | L | pull_request | test/issue115-claude-readme-policy-pilot-v1 | 0cec8f47a753aa3fba978263d991d7e7baa9ffe0 | UNKNOWN | 2026-10-11T15:52:34Z | 2026-10-11T15:52:34Z | 2026-10-11T15:55:19Z | completed/success | 1 |
| 38153764494 | L | pull_request | test/issue115-codex-109-pilot-v1 | d970635333857516916cd1b08535338b59b1a3ea | UNKNOWN | 2026-10-11T16:02:05Z | 2026-10-11T16:02:05Z | 2026-10-11T16:05:01Z | completed/success | 1 |
| 38157611096 | L | push | main | 5bcfb5e40aa8e94942365d81bbe3ec4609bfaced | UNKNOWN | 2026-10-11T16:57:29Z | 2026-10-11T16:57:29Z | 2026-10-11T17:00:33Z | completed/success | 1 |
| 38158003040 | L | push | main | 32f7a56e1ccd96292446b675033ce863e976b3e3 | UNKNOWN | 2026-10-11T17:03:01Z | 2026-10-11T17:03:01Z | 2026-10-11T17:06:04Z | completed/success | 1 |
| 38180591256 | L | pull_request | fix/llmtool-issue122-fixture-lifetime | e2270eb92d4c8044901ad6a33cfbcf28bc30379e | UNKNOWN | 2026-10-11T22:28:59Z | 2026-10-11T22:28:59Z | 2026-10-11T22:31:24Z | completed/success | 1 |
| 38199694423 | L | pull_request | fix/llmtool-issue122-fixture-lifetime | 51443423eed2bc8764aba7a36f5e2f615b29c48f | UNKNOWN | 2026-10-12T03:25:32Z | 2026-10-12T03:25:32Z | 2026-10-12T03:28:34Z | completed/success | 1 |
| 38209410257 | L | push | main | 2ba7925d1bb7bc22732642f2358b1ba0aab5718c | UNKNOWN | 2026-10-12T05:46:24Z | 2026-10-12T05:46:24Z | 2026-10-12T05:49:12Z | completed/success | 1 |
| 38210318808 | L | pull_request | docs/g7-two-worktrees-activation-20261012 | 34e3b872459a6ddc9c28503c9560a4574f68a08d | UNKNOWN | 2026-10-12T05:58:33Z | 2026-10-12T05:58:33Z | 2026-10-12T06:18:37Z | completed/cancelled | 1 |
| 38218481955 | L | pull_request | docs/g7-two-worktrees-activation-20261012 | 8a85b7a44245f418b2345bebd45439f70f0f7977 | UNKNOWN | 2026-10-12T07:34:19Z | 2026-10-12T07:34:19Z | 2026-10-12T07:37:35Z | completed/success | 1 |
| 38221849160 | L | push | main | 83be9f6294c7fda515a1ec939823d02b65aa2e4d | UNKNOWN | 2026-10-12T08:09:17Z | 2026-10-12T08:09:17Z | 2026-10-12T08:12:29Z | completed/success | 1 |
| 38245293673 | L | pull_request | docs/codex-issue120-s1a-contract-20261012-v1 | 8842cc0ec3d501611abd99806ab2728ed07dde7d | UNKNOWN | 2026-10-12T11:50:55Z | 2026-10-12T11:50:55Z | 2026-10-12T11:54:37Z | completed/success | 1 |
| 38246547109 | L | push | main | 8a92ced1fa92e3f5c4bc7b0378c4bdadb0208c6a | UNKNOWN | 2026-10-12T12:02:42Z | 2026-10-12T12:02:42Z | 2026-10-12T12:05:36Z | completed/success | 1 |
| 38253155542 | L | pull_request | feat/codex-issue120-s1a-product-v1-20261012 | c9c8747b59ac49b18dbc521e8af9ac7f803212fe | UNKNOWN | 2026-10-12T13:04:29Z | 2026-10-12T13:04:29Z | 2026-10-12T13:07:50Z | completed/success | 1 |
| 38267989170 | L | push | main | ffb4c78e72acadcdbb17d4470c4bf4a051cce247 | UNKNOWN | 2026-10-12T15:08:01Z | 2026-10-12T15:08:01Z | 2026-10-12T15:12:07Z | completed/success | 1 |
| 38272206529 | L | pull_request | docs/codex-issue120-s1b-contract-v1-20261012 | 9d0620fde7543e3a1b54eb727c131bbb1447b1a0 | UNKNOWN | 2026-10-12T15:42:32Z | 2026-10-12T15:42:32Z | 2026-10-12T15:45:51Z | completed/success | 1 |
| 38274490351 | L | push | main | 8468164d5503953f941b24c1f2318d5decda42b5 | UNKNOWN | 2026-10-12T16:01:37Z | 2026-10-12T16:01:37Z | 2026-10-12T16:04:32Z | completed/success | 1 |
| 38277738004 | L | pull_request | docs/claude-issue120-s1b-b-contract-v1-20261013 | 135b374ac8ecbf6a79d06c1667cf9dd214176e98 | UNKNOWN | 2026-10-12T16:29:22Z | 2026-10-12T16:29:22Z | 2026-10-12T16:31:48Z | completed/success | 1 |
| 38279956899 | L | pull_request | docs/claude-issue120-s1b-b-contract-v1-20261013 | af8c53b0d3b3f8384277c624b4b5dbe47db8dd75 | UNKNOWN | 2026-10-12T16:48:25Z | 2026-10-12T16:48:25Z | 2026-10-12T16:51:54Z | completed/success | 1 |
| 38281000368 | L | push | main | cfa6c73e26cf6a16bc3d8a2303a45a4a21904559 | UNKNOWN | 2026-10-12T16:57:36Z | 2026-10-12T16:57:36Z | 2026-10-12T17:00:52Z | completed/success | 1 |
| 38287371462 | L | pull_request | feat/codex-issue120-s1b-product-a-v1-20261013 | 5b16a2c231e2debbd835b4838dce81d4f09a2b35 | UNKNOWN | 2026-10-12T17:54:49Z | 2026-10-12T17:54:49Z | 2026-10-12T17:58:20Z | completed/success | 1 |
| 38289742740 | L | push | main | 055d1828d12cd8a4294613042ec4bc6e216a754a | UNKNOWN | 2026-10-12T18:15:59Z | 2026-10-12T18:15:59Z | 2026-10-12T18:19:25Z | completed/success | 1 |
| 38306539321 | L | pull_request | feat/codex-issue120-s1b-b1-v1-20261013 | 7916e38ab88236263cded8d3c2d00a05141d1a71 | UNKNOWN | 2026-10-12T20:47:56Z | 2026-10-12T20:47:56Z | 2026-10-12T20:51:14Z | completed/success | 1 |
| 38309800126 | L | push | main | 82179434da7aa257ec2ccbeeaff50977877423c0 | UNKNOWN | 2026-10-12T21:18:29Z | 2026-10-12T21:18:29Z | 2026-10-12T21:21:42Z | completed/success | 1 |
| 38423526992 | L | pull_request | feat/claude-issue135-codex-audit-521172ae | c68cd08ec9a25dee2e639d578fe017545063c806 | UNKNOWN | 2026-10-13T15:36:33Z | 2026-10-13T15:36:33Z | 2026-10-13T15:40:02Z | completed/success | 1 |
| 38438557573 | L | pull_request | feat/claude-issue135-codex-audit-521172ae | dcbc07f7144b2b43a3ead01bfbe97ed5afbe38d5 | UNKNOWN | 2026-10-13T17:25:40Z | 2026-10-13T17:25:40Z | 2026-10-13T17:29:07Z | completed/success | 1 |
| 38441600511 | L | pull_request | feat/claude-issue135-codex-audit-521172ae | 7f07f6b7758dc51015b591845f8fbd0066567edb | UNKNOWN | 2026-10-13T17:48:14Z | 2026-10-13T17:48:14Z | 2026-10-13T17:51:30Z | completed/success | 1 |
| 38448199612 | L | pull_request | feat/claude-issue135-codex-audit-521172ae | 41323db2aec955f4c68e6e966d7e1f2c2edfc2f2 | UNKNOWN | 2026-10-13T18:37:05Z | 2026-10-13T18:37:05Z | 2026-10-13T18:40:15Z | completed/success | 1 |
| 38449831149 | L | push | main | 8c79381afb49acca519091deda326c039b92227c | UNKNOWN | 2026-10-13T18:49:09Z | 2026-10-13T18:49:09Z | 2026-10-13T18:53:01Z | completed/success | 1 |
| 38464881760 | L | pull_request | feat/codex-issue135-native-hooks-3b6aad90-20261014 | 670fb3c209b20417e478651b1ef2cd857a619204 | UNKNOWN | 2026-10-13T20:41:54Z | 2026-10-13T20:41:54Z | 2026-10-13T20:46:07Z | completed/success | 1 |
| 38467244446 | L | push | main | 25e0f5b932541fab2ec1ef9c47bf736a2c8887bc | UNKNOWN | 2026-10-13T21:00:10Z | 2026-10-13T21:00:10Z | 2026-10-13T21:03:14Z | completed/success | 1 |
| 38473047009 | L | pull_request | docs/owner-workpackage-subagent-20261014-25e0f5b9 | 8db963baae4b2dceb32dccb86a8e9bc43dd269d5 | UNKNOWN | 2026-10-13T21:46:10Z | 2026-10-13T21:46:10Z | 2026-10-13T21:49:18Z | completed/success | 1 |
| 38474148180 | L | push | main | 86080cd757d2786c65d848943cf35f063d11f6fc | UNKNOWN | 2026-10-13T21:55:24Z | 2026-10-13T21:55:24Z | 2026-10-13T21:58:55Z | completed/success | 1 |
| 38475645512 | L | pull_request | feat/codex-issue135-track4-l1l2-25e0f5b9-20261014 | ab43be4b33a5da79b9c02b83e02df535ec440628 | UNKNOWN | 2026-10-13T22:07:57Z | 2026-10-13T22:07:57Z | 2026-10-13T22:08:07Z | completed/failure | 1 |
| 38512141589 | L | pull_request | feat/codex-issue135-track4-l1l2-25e0f5b9-20261014 | 42d0c3817c64318671bab99d9cfdd61031e03fc7 | UNKNOWN | 2026-10-14T04:35:04Z | 2026-10-14T04:35:04Z | 2026-10-14T04:38:31Z | completed/success | 1 |
| 38521001723 | L | push | main | 73335858ad4655274a16e655b64f0f661a104ac0 | UNKNOWN | 2026-10-14T06:13:56Z | 2026-10-14T06:13:56Z | 2026-10-14T06:17:38Z | completed/success | 1 |
| 38525138561 | L | pull_request | feat/claude-issue66-final-excerpt-privacy-73335858 | 75052d115b27d92b63651ca48499fc0d8ec59928 | UNKNOWN | 2026-10-14T06:55:57Z | 2026-10-14T06:55:57Z | 2026-10-14T06:59:28Z | completed/success | 1 |
| 38530073504 | L | push | main | 6fba5874df4fad810c33db368601ba504459fa5a | UNKNOWN | 2026-10-14T07:42:20Z | 2026-10-14T07:42:20Z | 2026-10-14T07:45:41Z | completed/success | 1 |
| 38531941680 | L | pull_request | docs/codex-issue120-a11c-cross-cli-handoff-6fba5874-20261014 | ceb7f086781aa95601626066760971196f52388c | UNKNOWN | 2026-10-14T07:59:01Z | 2026-10-14T07:59:01Z | 2026-10-14T08:02:37Z | completed/success | 1 |
| 38534116003 | L | push | main | fd04f64013d8ea55e97806dc78707228abd546ed | UNKNOWN | 2026-10-14T08:17:55Z | 2026-10-14T08:17:55Z | 2026-10-14T08:23:18Z | completed/success | 1 |
| 38545074043 | L | pull_request | feat/codex-issue120-s1b-handoff-fake-a1-20261014 | 36acc7609c05c74484f1c8b26d5ee2659577009b | UNKNOWN | 2026-10-14T09:48:38Z | 2026-10-14T09:48:38Z | 2026-10-14T09:52:10Z | completed/success | 1 |
| 38547994371 | L | push | main | a17f30c6cc0f3a60fde6d0a5156b90f258bb923d | UNKNOWN | 2026-10-14T10:12:59Z | 2026-10-14T10:12:59Z | 2026-10-14T10:16:51Z | completed/success | 1 |
| 38556615717 | L | pull_request | feat/codex-issue120-s1b-codex-pickup-b1-20261014 | 2cdd42aa7fd243d0f8c02d34b25b01488c7fd70e | UNKNOWN | 2026-10-14T11:26:48Z | 2026-10-14T11:26:48Z | 2026-10-14T11:30:38Z | completed/success | 1 |
| 38561729278 | L | push | main | e526c6145a0fb77de6af56a653f15e50f57c74eb | UNKNOWN | 2026-10-14T12:09:34Z | 2026-10-14T12:09:34Z | 2026-10-14T12:13:04Z | completed/success | 1 |
| 38603255335 | L | pull_request | feat/codex-issue120-native-delta-a1-20261014 | 102e1b76237e926e7cc1103f33181dc4302358e1 | UNKNOWN | 2026-10-14T17:02:39Z | 2026-10-14T17:02:39Z | 2026-10-14T17:06:11Z | completed/success | 1 |
| 38606890445 | L | pull_request | feat/codex-issue120-native-delta-a1-20261014 | 79a08a872f75a72bc611d07ee776b4116e9d4c4e | UNKNOWN | 2026-10-14T17:28:05Z | 2026-10-14T17:28:05Z | 2026-10-14T17:32:00Z | completed/success | 1 |
| 38607227388 | L | pull_request | feat/claude-issue135-r29-write-coverage-e526c614 | 864f93f132ef3c31187503bda4782f199c798ede | UNKNOWN | 2026-10-14T17:30:28Z | 2026-10-14T17:30:28Z | 2026-10-14T17:39:31Z | completed/success | 1 |
| 38608025840 | L | push | main | 159a2441511d08da3719aa9631c46947b9f0d997 | UNKNOWN | 2026-10-14T17:36:04Z | 2026-10-14T17:36:04Z | 2026-10-14T17:39:04Z | completed/success | 1 |
| 38609513693 | L | pull_request | feat/claude-issue135-r29-write-coverage-e526c614 | 659798db6040ad465e66cc3e17b68c3362dde0cc | UNKNOWN | 2026-10-14T17:46:35Z | 2026-10-14T17:46:35Z | 2026-10-14T17:50:19Z | completed/success | 1 |
| 38611201640 | L | push | main | 66ba57cae2e7cf9b323624fbb8c975ab51c9ed02 | UNKNOWN | 2026-10-14T17:58:30Z | 2026-10-14T17:58:30Z | 2026-10-14T18:01:26Z | completed/success | 1 |
| 38635628747 | L | pull_request | feat/codex-issue120-native-composition-a1-20261015 | 9835112140e8e1040003812b2b5b9290604b44e8 | UNKNOWN | 2026-10-14T20:51:49Z | 2026-10-14T20:51:49Z | 2026-10-14T20:55:27Z | completed/success | 1 |
| 38638182792 | L | push | main | acd462c0ccafdb2c2e1c29847a03d90e12f89adc | UNKNOWN | 2026-10-14T21:10:35Z | 2026-10-14T21:10:35Z | 2026-10-14T21:14:18Z | completed/success | 1 |
| 38638688707 | L | pull_request | feat/claude-issue120-c1b-triage-worker-v1-20261015 | 79bc0db7ecc691711cc90c547e2e96e9e5de6317 | UNKNOWN | 2026-10-14T21:14:25Z | 2026-10-14T21:14:25Z | 2026-10-14T21:14:34Z | completed/failure | 1 |
| 38639165928 | L | pull_request | feat/claude-issue120-c1b-triage-worker-v1-20261015 | e0dfbc4b0e42349f2d060d0446f823f8e35f447c | UNKNOWN | 2026-10-14T21:18:01Z | 2026-10-14T21:18:01Z | 2026-10-14T21:21:49Z | completed/success | 1 |
| 38640853573 | L | pull_request | feat/claude-issue120-c1b-triage-worker-v1-20261015 | 1506758d08eebff9eceec8ff619ed20f1faeb8de | UNKNOWN | 2026-10-14T21:30:56Z | 2026-10-14T21:30:56Z | 2026-10-14T21:34:28Z | completed/success | 1 |
| 38677838050 | L | push | main | 6306f2dc9b9b2d618f30374333b9b170a2854996 | UNKNOWN | 2026-10-15T03:44:36Z | 2026-10-15T03:44:36Z | 2026-10-15T03:48:08Z | completed/success | 1 |
| 38688774516 | L | pull_request | feat/codex-issue120-cr-k2b-host-rollout-v1-20261015 | 7935c12c326e3cf80400569d3ad822adf0aaabec | UNKNOWN | 2026-10-15T05:48:20Z | 2026-10-15T05:48:20Z | 2026-10-15T05:51:43Z | completed/success | 1 |
| 38707624510 | L | pull_request | feat/codex-issue120-cr-k2b-host-rollout-v1-20261015 | f6b0aa6297c7f0699f2ba4b57438ca93ac25b57a | UNKNOWN | 2026-10-15T08:42:48Z | 2026-10-15T08:42:48Z | 2026-10-15T08:46:43Z | completed/success | 1 |
| 38713915448 | L | push | main | b8582dddc906472e9b24d011948f08d0ea04ab6c | UNKNOWN | 2026-10-15T09:34:19Z | 2026-10-15T09:34:19Z | 2026-10-15T09:38:19Z | completed/success | 1 |
| 38726054334 | L | pull_request | feat/codex-issue120-p0-live-seam-v1-20261015 | 1fb97e73d230f633d98d4330641806168fb24663 | UNKNOWN | 2026-10-15T11:15:35Z | 2026-10-15T11:15:35Z | 2026-10-15T11:19:10Z | completed/success | 1 |
| 38738877878 | L | push | main | 63dc90ff413ac6bb65d896b63fd3bea9519e8887 | UNKNOWN | 2026-10-15T12:58:57Z | 2026-10-15T12:58:57Z | 2026-10-15T13:03:02Z | completed/success | 1 |
| 38744466254 | L | pull_request | feat/codex-issue120-native-step0-census-driver-v1-20261015 | 1704fdf7647db3d075968c7addbe72f39d078a3d | UNKNOWN | 2026-10-15T13:38:48Z | 2026-10-15T13:38:48Z | 2026-10-15T13:42:05Z | completed/failure | 1 |
| 38745290285 | L | pull_request | feat/codex-issue120-native-step0-census-driver-v1-20261015 | a1391c6476508a9bb31faf429396624898ff21f9 | UNKNOWN | 2026-10-15T13:44:33Z | 2026-10-15T13:44:33Z | 2026-10-15T13:48:35Z | completed/success | 1 |
| 38759496913 | L | pull_request | feat/codex-issue120-native-step0-census-driver-v1-20261015 | f41a850ab97d650180ffcaee7a4598ff616346e9 | UNKNOWN | 2026-10-15T15:20:47Z | 2026-10-15T15:20:47Z | 2026-10-15T15:24:43Z | completed/failure | 1 |
| 38760778015 | L | pull_request | feat/codex-issue120-native-step0-census-driver-v1-20261015 | 1ec45205f7bbafc6a48fa26c857102a1850fb41d | UNKNOWN | 2026-10-15T15:29:28Z | 2026-10-15T15:29:28Z | 2026-10-15T15:33:17Z | completed/success | 1 |
| 38762900881 | L | push | main | 7d86734c018f571f19452e770b84f5b0c0b7aeee | UNKNOWN | 2026-10-15T15:43:56Z | 2026-10-15T15:43:56Z | 2026-10-15T15:48:05Z | completed/success | 1 |
| 38763930750 | L | pull_request | docs/issue120-self-hosting-first-m1-20261015 | 8ed76c59a1d25141ae4b99dfaf098bfc195160bd | UNKNOWN | 2026-10-15T15:51:01Z | 2026-10-15T15:51:01Z | 2026-10-15T15:54:49Z | completed/success | 1 |
| 38768046178 | L | pull_request | feat/codex-issue120-m1b-census-d1-20261015 | 08c6b826eed5dc942b471b286b014fbdb41f382b | UNKNOWN | 2026-10-15T16:19:38Z | 2026-10-15T16:19:38Z | 2026-10-15T16:24:20Z | completed/success | 1 |
| 38772118466 | L | push | main | 95aee6a5fa6cde7f2c9166e6eaca2e18ece45e74 | UNKNOWN | 2026-10-15T16:48:53Z | 2026-10-15T16:48:53Z | 2026-10-15T16:53:52Z | completed/success | 1 |
| 38780829803 | L | push | main | b440cb66106785868e81718b251a7e6b22ea7b51 | UNKNOWN | 2026-10-15T17:51:42Z | 2026-10-15T17:51:42Z | 2026-10-15T17:55:24Z | completed/success | 1 |
| 38786307663 | L | pull_request | feat/codex-issue120-m2-review-bundle-v1-20261016 | 18765bcb44432e5ba5b0197da709b02a176971a8 | UNKNOWN | 2026-10-15T18:31:17Z | 2026-10-15T18:31:17Z | 2026-10-15T18:35:17Z | completed/success | 1 |
| 38788519620 | L | push | main | 67f5522a81bf5b1020e06f572502120b65359033 | UNKNOWN | 2026-10-15T18:47:21Z | 2026-10-15T18:47:21Z | 2026-10-15T18:51:51Z | completed/success | 1 |
| 38840614300 | L | pull_request | feat/codex-issue120-m2-review-roundtrip-v1-20261016 | 13d0952abad4febc155b5f120b8659eebc0e4973 | UNKNOWN | 2026-10-16T02:28:36Z | 2026-10-16T02:28:36Z | 2026-10-16T02:32:34Z | completed/success | 1 |
| 38852907300 | L | push | main | 9fa73774b036f0cb064de9a44c453e07b4112b69 | UNKNOWN | 2026-10-16T04:53:12Z | 2026-10-16T04:53:12Z | 2026-10-16T04:57:12Z | completed/success | 1 |
| 38904702584 | L | pull_request | feat/codex-issue120-m2-delivery-consumer-v1-20261016 | 52fa52276989a660773bc1d3742b0cb2254a160f | UNKNOWN | 2026-10-16T13:01:26Z | 2026-10-16T13:01:26Z | 2026-10-16T13:05:39Z | completed/success | 1 |
| 38906508369 | L | push | main | 6d04c4fc8517286bb4d5d1a95e7c32109e342c88 | UNKNOWN | 2026-10-16T13:15:48Z | 2026-10-16T13:15:48Z | 2026-10-16T13:19:43Z | completed/success | 1 |
| 38915460898 | L | pull_request | feat/codex-issue120-m2-reviewer-job-v1-20261016 | c17ed3988377c11b289942083315696458801c5e | UNKNOWN | 2026-10-16T14:24:06Z | 2026-10-16T14:24:06Z | 2026-10-16T14:28:14Z | completed/success | 1 |
| 38921008623 | L | push | main | 86a990eb4389044aa3196f25d679c2d73b4591a3 | UNKNOWN | 2026-10-16T15:04:56Z | 2026-10-16T15:04:56Z | 2026-10-16T15:09:22Z | completed/success | 1 |
| 38929856416 | L | pull_request | fix/codex-issue120-m2-wj1-cleanup-v1-20261016 | 5e58382214f6ae53217edc53c5b63850aa920fef | UNKNOWN | 2026-10-16T16:10:36Z | 2026-10-16T16:10:36Z | 2026-10-16T16:14:49Z | completed/success | 1 |
| 38931195385 | L | push | main | 9fa1aaba93b8cfe1d95179a392b2ad33bba54b39 | UNKNOWN | 2026-10-16T16:20:39Z | 2026-10-16T16:20:39Z | 2026-10-16T16:25:09Z | completed/success | 1 |
| 38953809734 | L | pull_request | feat/codex-issue120-m2-foreground-entry-v1-20261017 | 00855ad21b6b81bf1c8dd959127c58955f0b768c | UNKNOWN | 2026-10-16T19:17:23Z | 2026-10-16T19:17:23Z | 2026-10-16T19:21:37Z | completed/success | 1 |
| 38955292748 | L | push | main | cd7ee6494938df469ce7c9fc3f8b8948d9354ed9 | UNKNOWN | 2026-10-16T19:29:27Z | 2026-10-16T19:29:27Z | 2026-10-16T19:33:41Z | completed/success | 1 |
| 38974313605 | L | pull_request | docs/llmtool-cloud-lane-roadmap-sync-20261017 | 5905976438a556af75d9039102e3488b66c43e13 | 174:113eeec5a582c10d9fc5e5e4faf4a4dbf3edc907:cd7ee6494938df469ce7c9fc3f8b8948d9354ed9 | 2026-10-16T22:12:14Z | 2026-10-16T22:12:14Z | 2026-10-16T22:16:12Z | completed/success | 1 |
| 38975543979 | L | pull_request | docs/llmtool-cloud-lane-roadmap-sync-20261017 | 113eeec5a582c10d9fc5e5e4faf4a4dbf3edc907 | 174:113eeec5a582c10d9fc5e5e4faf4a4dbf3edc907:cd7ee6494938df469ce7c9fc3f8b8948d9354ed9 | 2026-10-16T22:24:20Z | 2026-10-16T22:24:20Z | 2026-10-16T22:28:54Z | completed/success | 1 |
| 38986578072 | L | pull_request | fix/codex-issue177-deterministic-ledger-lock | 593a2e1edabf0b61e9b7282afcd099fb6d133d55 | 178:593a2e1edabf0b61e9b7282afcd099fb6d133d55:cd7ee6494938df469ce7c9fc3f8b8948d9354ed9 | 2026-10-17T00:29:08Z | 2026-10-17T00:29:08Z | 2026-10-17T00:33:13Z | completed/success | 1 |
| 38986751555 | L | pull_request | fix/codex-issue175-retrieval-exclusion-counts | 94fa37c2d2c7c6e5480677dd29bf6842638424df | 179:94fa37c2d2c7c6e5480677dd29bf6842638424df:cd7ee6494938df469ce7c9fc3f8b8948d9354ed9 | 2026-10-17T00:31:19Z | 2026-10-17T00:31:19Z | 2026-10-17T00:35:25Z | completed/success | 1 |
| 38987176840 | L | pull_request | fix/codex-issue176-doctor-store-truthfulness | 2e270b7f813d53c2141cfa14c2747de21cface66 | 180:2e270b7f813d53c2141cfa14c2747de21cface66:cd7ee6494938df469ce7c9fc3f8b8948d9354ed9 | 2026-10-17T00:36:52Z | 2026-10-17T00:36:52Z | 2026-10-17T00:40:58Z | completed/success | 1 |

## Appendix B — frozen job ledger, including all attempts

Job URLs: `https://github.com/davidcatcat/davidcattoolsproject/actions/runs/<Run>/job/<Job>`. Labels are API labels, not billing SKUs. Every job status is completed; conclusions remain literal. Seconds are reported timestamp subtraction only, UNKNOWN for the anomalous row. Distinct job IDs are retained even when timestamps match on reruns. Zero-job cancelled run IDs38069201639,38069250144,38069366690 have no rows here.

| Run | Attempt | Job ID | Name | Runner label | Started UTC | Completed UTC | Conclusion | Valid wall seconds |
| --- | ---: | --- | --- | --- | --- | --- | --- | ---: |
| 38034723327 | 1 | 114162675159 | check (3.14) | ubuntu-latest | 2026-10-10T07:31:39Z | 2026-10-10T07:33:57Z | failure | 138 |
| 38034723327 | 1 | 114162675310 | check (3.9) | ubuntu-latest | 2026-10-10T07:31:39Z | 2026-10-10T07:34:13Z | success | 154 |
| 38034725730 | 1 | 114162683152 | check (3.9) | ubuntu-latest | 2026-10-10T07:31:42Z | 2026-10-10T07:33:28Z | success | 106 |
| 38034725730 | 1 | 114162683288 | check (3.14) | ubuntu-latest | 2026-10-10T07:31:41Z | 2026-10-10T07:33:54Z | failure | 133 |
| 38036173975 | 1 | 114166939279 | check (3.14) | ubuntu-latest | 2026-10-10T07:56:54Z | 2026-10-10T07:59:17Z | success | 143 |
| 38036173975 | 1 | 114166939465 | check (3.9) | ubuntu-latest | 2026-10-10T07:56:54Z | 2026-10-10T07:59:10Z | success | 136 |
| 38036176798 | 1 | 114166948740 | check (3.14) | ubuntu-latest | 2026-10-10T07:56:57Z | 2026-10-10T08:00:04Z | success | 187 |
| 38036176798 | 1 | 114166948898 | check (3.9) | ubuntu-latest | 2026-10-10T07:56:57Z | 2026-10-10T07:59:27Z | success | 150 |
| 38038624603 | 1 | 114174241339 | check (3.14) | ubuntu-latest | 2026-10-10T08:39:16Z | 2026-10-10T08:42:10Z | success | 174 |
| 38038624603 | 1 | 114174241484 | check (3.9) | ubuntu-latest | 2026-10-10T08:39:16Z | 2026-10-10T08:41:48Z | success | 152 |
| 38038627671 | 1 | 114174250766 | check (3.14) | ubuntu-latest | 2026-10-10T08:39:19Z | 2026-10-10T08:42:25Z | success | 186 |
| 38038627671 | 1 | 114174250874 | check (3.9) | ubuntu-latest | 2026-10-10T08:39:19Z | 2026-10-10T08:41:39Z | success | 140 |
| 38048295596 | 1 | 114202154212 | check (3.9) | ubuntu-latest | 2026-10-10T11:24:29Z | 2026-10-10T11:26:43Z | success | 134 |
| 38048295596 | 1 | 114202154395 | check (3.14) | ubuntu-latest | 2026-10-10T11:24:29Z | 2026-10-10T11:27:30Z | success | 181 |
| 38048297938 | 1 | 114202161704 | check (3.9) | ubuntu-latest | 2026-10-10T11:24:31Z | 2026-10-10T11:27:02Z | success | 151 |
| 38048297938 | 1 | 114202161807 | check (3.14) | ubuntu-latest | 2026-10-10T11:24:37Z | 2026-10-10T11:27:03Z | success | 146 |
| 38050791882 | 1 | 114209331074 | check (3.9) | ubuntu-latest | 2026-10-10T12:07:01Z | 2026-10-10T12:09:47Z | success | 166 |
| 38050791882 | 1 | 114209331113 | check (3.14) | ubuntu-latest | 2026-10-10T12:07:01Z | 2026-10-10T12:09:28Z | success | 147 |
| 38050792488 | 1 | 114209332503 | check (3.14) | ubuntu-latest | 2026-10-10T12:07:02Z | 2026-10-10T12:09:54Z | success | 172 |
| 38050792488 | 1 | 114209332737 | check (3.9) | ubuntu-latest | 2026-10-10T12:07:01Z | 2026-10-10T12:09:32Z | success | 151 |
| 38055456483 | 1 | 114222862840 | check (3.9) | ubuntu-latest | 2026-10-10T13:22:21Z | 2026-10-10T13:24:44Z | success | 143 |
| 38055456483 | 1 | 114222863003 | check (3.14) | ubuntu-latest | 2026-10-10T13:22:21Z | 2026-10-10T13:25:08Z | success | 167 |
| 38055461497 | 1 | 114222878335 | check (3.9) | ubuntu-latest | 2026-10-10T13:22:26Z | 2026-10-10T13:24:07Z | success | 101 |
| 38055461497 | 1 | 114222878500 | check (3.14) | ubuntu-latest | 2026-10-10T13:22:26Z | 2026-10-10T13:24:54Z | success | 148 |
| 38068367007 | 1 | 114260540391 | check (3.9) | ubuntu-latest | 2026-10-10T16:36:45Z | 2026-10-10T16:39:16Z | success | 151 |
| 38068367007 | 1 | 114260540474 | check (3.14) | ubuntu-latest | 2026-10-10T16:36:44Z | 2026-10-10T16:39:36Z | success | 172 |
| 38069175751 | 1 | 114262904049 | check (3.14) | ubuntu-latest | 2026-10-10T16:49:00Z | 2026-10-10T16:51:50Z | success | 170 |
| 38069175751 | 1 | 114262904284 | check (3.9) | ubuntu-latest | 2026-10-10T16:49:00Z | 2026-10-10T16:51:32Z | success | 152 |
| 38069373419 | 1 | 114263477155 | check (3.14) | ubuntu-latest | 2026-10-10T16:51:53Z | 2026-10-10T16:54:44Z | success | 171 |
| 38069373419 | 1 | 114263477282 | check (3.9) | ubuntu-latest | 2026-10-10T16:51:53Z | 2026-10-10T16:54:23Z | success | 150 |
| 38069442367 | 1 | 114263675811 | check (3.9) | ubuntu-latest | 2026-10-10T16:52:50Z | 2026-10-10T16:55:21Z | success | 151 |
| 38069442367 | 1 | 114263676502 | check (3.14) | ubuntu-latest | 2026-10-10T16:52:51Z | 2026-10-10T16:55:42Z | success | 171 |
| 38069799930 | 1 | 114264714363 | check (3.14) | ubuntu-latest | 2026-10-10T16:58:07Z | 2026-10-10T16:58:37Z | cancelled | 30 |
| 38069799930 | 1 | 114264714582 | check (3.9) | ubuntu-latest | 2026-10-10T16:58:07Z | 2026-10-10T16:58:24Z | cancelled | 17 |
| 38069813409 | 1 | 114264754288 | check (3.14) | ubuntu-latest | 2026-10-10T16:58:19Z | 2026-10-10T17:01:10Z | success | 171 |
| 38069813409 | 1 | 114264754468 | check (3.9) | ubuntu-latest | 2026-10-10T16:58:19Z | 2026-10-10T17:00:28Z | success | 129 |
| 38069818376 | 1 | 114264821810 | check (3.9) | ubuntu-latest | 2026-10-10T16:58:39Z | 2026-10-10T17:00:52Z | success | 133 |
| 38069818376 | 1 | 114264821962 | check (3.14) | ubuntu-latest | 2026-10-10T16:58:40Z | 2026-10-10T17:01:24Z | success | 164 |
| 38070628943 | 1 | 114267116118 | check (3.9) | ubuntu-latest | 2026-10-10T17:10:10Z | 2026-10-10T17:11:56Z | success | 106 |
| 38070628943 | 1 | 114267116264 | check (3.14) | ubuntu-latest | 2026-10-10T17:10:10Z | 2026-10-10T17:12:51Z | success | 161 |
| 38073072812 | 1 | 114274312231 | helper (3.14) | ubuntu-latest | 2026-10-10T17:45:59Z | 2026-10-10T17:46:01Z | failure | 2 |
| 38073072812 | 1 | 114274312380 | helper (3.9) | ubuntu-latest | 2026-10-10T17:45:59Z | 2026-10-10T17:46:01Z | failure | 2 |
| 38073072812 | 2 | 114274459383 | helper (3.14) | ubuntu-latest | 2026-10-10T17:46:43Z | 2026-10-10T17:46:45Z | failure | 2 |
| 38073072812 | 2 | 114274459497 | helper (3.9) | ubuntu-latest | 2026-10-10T17:46:43Z | 2026-10-10T17:46:46Z | failure | 3 |
| 38073072812 | 3 | 114283781181 | helper (3.9) | ubuntu-latest | 2026-10-10T18:33:53Z | 2026-10-10T18:34:13Z | success | 20 |
| 38073072812 | 3 | 114283781796 | helper (3.14) | ubuntu-latest | 2026-10-10T18:33:52Z | 2026-10-10T17:46:45Z | failure | UNKNOWN |
| 38073072812 | 4 | 114289260452 | helper (3.14) | ubuntu-latest | 2026-10-10T19:01:16Z | 2026-10-10T19:01:27Z | success | 11 |
| 38073072812 | 4 | 114289261337 | helper (3.9) | ubuntu-latest | 2026-10-10T18:33:53Z | 2026-10-10T18:34:13Z | success | 20 |
| 38078206890 | 1 | 114289515448 | helper (3.14) | ubuntu-latest | 2026-10-10T19:02:33Z | 2026-10-10T19:02:41Z | success | 8 |
| 38078206890 | 1 | 114289515951 | helper (3.9) | ubuntu-latest | 2026-10-10T19:02:33Z | 2026-10-10T19:02:56Z | success | 23 |
| 38078689450 | 1 | 114290928796 | check | ubuntu-latest | 2026-10-10T19:09:44Z | 2026-10-10T19:15:01Z | success | 317 |
| 38079142443 | 1 | 114292263688 | check | ubuntu-latest | 2026-10-10T19:16:30Z | 2026-10-10T19:19:59Z | failure | 209 |
| 38081677441 | 1 | 114299705268 | check (3.14) | ubuntu-latest | 2026-10-10T19:55:01Z | 2026-10-10T19:56:59Z | success | 118 |
| 38081677441 | 1 | 114299705410 | check (3.9) | ubuntu-latest | 2026-10-10T19:55:01Z | 2026-10-10T19:57:34Z | success | 153 |
| 38082328745 | 1 | 114301628375 | check (3.14) | ubuntu-latest | 2026-10-10T20:04:57Z | 2026-10-10T20:07:07Z | success | 130 |
| 38082328745 | 1 | 114301628556 | check (3.9) | ubuntu-latest | 2026-10-10T20:04:58Z | 2026-10-10T20:07:33Z | success | 155 |
| 38082823896 | 1 | 114303081549 | helper (3.14) | ubuntu-latest | 2026-10-10T20:12:24Z | 2026-10-10T20:12:33Z | success | 9 |
| 38082823896 | 1 | 114303081697 | helper (3.9) | ubuntu-latest | 2026-10-10T20:12:24Z | 2026-10-10T20:12:41Z | success | 17 |
| 38087138863 | 1 | 114315900594 | check (3.14) | ubuntu-latest | 2026-10-10T21:18:59Z | 2026-10-10T21:22:16Z | success | 197 |
| 38087138863 | 1 | 114315900729 | check (3.9) | ubuntu-latest | 2026-10-10T21:18:59Z | 2026-10-10T21:20:55Z | success | 116 |
| 38088365926 | 1 | 114319528021 | check (3.14) | ubuntu-latest | 2026-10-10T21:38:03Z | 2026-10-10T21:40:51Z | success | 168 |
| 38088365926 | 1 | 114319528187 | check (3.9) | ubuntu-latest | 2026-10-10T21:38:03Z | 2026-10-10T21:40:19Z | success | 136 |
| 38089623011 | 1 | 114323214613 | check (3.14) | ubuntu-latest | 2026-10-10T21:58:05Z | 2026-10-10T22:00:53Z | success | 168 |
| 38089623011 | 1 | 114323214675 | check (3.9) | ubuntu-latest | 2026-10-10T21:58:05Z | 2026-10-10T21:59:44Z | success | 99 |
| 38091109467 | 1 | 114327555636 | check (3.9) | ubuntu-latest | 2026-10-10T22:21:24Z | 2026-10-10T22:23:59Z | success | 155 |
| 38091109467 | 1 | 114327555908 | check (3.14) | ubuntu-latest | 2026-10-10T22:21:24Z | 2026-10-10T22:24:10Z | success | 166 |
| 38092274997 | 1 | 114330978086 | check (3.9) | ubuntu-latest | 2026-10-10T22:40:06Z | 2026-10-10T22:42:43Z | success | 157 |
| 38092274997 | 1 | 114330978283 | check (3.14) | ubuntu-latest | 2026-10-10T22:40:06Z | 2026-10-10T22:43:00Z | success | 174 |
| 38112341344 | 1 | 114390329851 | check (3.9) | ubuntu-latest | 2026-10-11T04:40:46Z | 2026-10-11T04:43:22Z | success | 156 |
| 38112341344 | 1 | 114390329916 | check (3.14) | ubuntu-latest | 2026-10-11T04:40:46Z | 2026-10-11T04:43:39Z | success | 173 |
| 38113627544 | 1 | 114394073163 | check (3.9) | ubuntu-latest | 2026-10-11T05:04:51Z | 2026-10-11T05:07:13Z | success | 142 |
| 38113627544 | 1 | 114394073330 | check (3.14) | ubuntu-latest | 2026-10-11T05:04:51Z | 2026-10-11T05:07:42Z | success | 171 |
| 38114610903 | 1 | 114396916579 | check (3.9) | ubuntu-latest | 2026-10-11T05:23:13Z | 2026-10-11T05:25:51Z | success | 158 |
| 38114610903 | 1 | 114396916663 | check (3.14) | ubuntu-latest | 2026-10-11T05:23:13Z | 2026-10-11T05:25:48Z | success | 155 |
| 38115959118 | 1 | 114400778799 | check (3.14) | ubuntu-latest | 2026-10-11T05:48:03Z | 2026-10-11T05:50:58Z | success | 175 |
| 38115959118 | 1 | 114400778878 | check (3.9) | ubuntu-latest | 2026-10-11T05:48:03Z | 2026-10-11T05:50:36Z | success | 153 |
| 38117034771 | 1 | 114403862502 | check (3.9) | ubuntu-latest | 2026-10-11T06:07:37Z | 2026-10-11T06:09:50Z | success | 133 |
| 38117034771 | 1 | 114403862576 | check (3.14) | ubuntu-latest | 2026-10-11T06:07:37Z | 2026-10-11T06:10:57Z | success | 200 |
| 38117407887 | 1 | 114404960710 | check (3.9) | ubuntu-latest | 2026-10-11T06:14:25Z | 2026-10-11T06:16:20Z | success | 115 |
| 38117407887 | 1 | 114404960754 | check (3.14) | ubuntu-latest | 2026-10-11T06:14:24Z | 2026-10-11T06:17:12Z | success | 168 |
| 38117619198 | 1 | 114405576847 | check (3.14) | ubuntu-latest | 2026-10-11T06:18:15Z | 2026-10-11T06:21:06Z | success | 171 |
| 38117619198 | 1 | 114405576972 | check (3.9) | ubuntu-latest | 2026-10-11T06:18:16Z | 2026-10-11T06:20:55Z | success | 159 |
| 38118418148 | 1 | 114407940028 | check (3.14) | ubuntu-latest | 2026-10-11T06:32:56Z | 2026-10-11T06:33:03Z | failure | 7 |
| 38118418148 | 1 | 114407940201 | check (3.9) | ubuntu-latest | 2026-10-11T06:32:57Z | 2026-10-11T06:33:03Z | failure | 6 |
| 38118512840 | 1 | 114408219023 | check (3.9) | ubuntu-latest | 2026-10-11T06:34:41Z | 2026-10-11T06:37:25Z | success | 164 |
| 38118512840 | 1 | 114408219145 | check (3.14) | ubuntu-latest | 2026-10-11T06:34:41Z | 2026-10-11T06:37:29Z | success | 168 |
| 38119486310 | 1 | 114411074568 | check (3.9) | ubuntu-latest | 2026-10-11T06:52:44Z | 2026-10-11T06:55:22Z | success | 158 |
| 38119486310 | 1 | 114411074768 | check (3.14) | ubuntu-latest | 2026-10-11T06:52:44Z | 2026-10-11T06:54:50Z | success | 126 |
| 38119684577 | 1 | 114411654512 | check (3.9) | ubuntu-latest | 2026-10-11T06:56:19Z | 2026-10-11T06:58:57Z | success | 158 |
| 38119684577 | 1 | 114411654679 | check (3.14) | ubuntu-latest | 2026-10-11T06:56:19Z | 2026-10-11T06:59:05Z | success | 166 |
| 38120752289 | 1 | 114414798206 | check (3.14) | ubuntu-latest | 2026-10-11T07:15:28Z | 2026-10-11T07:18:33Z | success | 185 |
| 38120752289 | 1 | 114414798395 | check (3.9) | ubuntu-latest | 2026-10-11T07:15:27Z | 2026-10-11T07:17:28Z | success | 121 |
| 38120919391 | 1 | 114415296621 | check (3.14) | ubuntu-latest | 2026-10-11T07:18:27Z | 2026-10-11T07:20:48Z | success | 141 |
| 38120919391 | 1 | 114415296769 | check (3.9) | ubuntu-latest | 2026-10-11T07:18:28Z | 2026-10-11T07:21:08Z | success | 160 |
| 38121336976 | 1 | 114416515363 | check (3.9) | ubuntu-latest | 2026-10-11T07:25:51Z | 2026-10-11T07:28:07Z | success | 136 |
| 38121336976 | 1 | 114416515490 | check (3.14) | ubuntu-latest | 2026-10-11T07:25:51Z | 2026-10-11T07:28:45Z | success | 174 |
| 38153108040 | 1 | 114510454552 | check (3.14) | ubuntu-latest | 2026-10-11T15:52:37Z | 2026-10-11T15:55:18Z | success | 161 |
| 38153108040 | 1 | 114510454571 | check (3.9) | ubuntu-latest | 2026-10-11T15:52:37Z | 2026-10-11T15:54:58Z | success | 141 |
| 38153764494 | 1 | 114512382644 | check (3.14) | ubuntu-latest | 2026-10-11T16:02:08Z | 2026-10-11T16:05:00Z | success | 172 |
| 38153764494 | 1 | 114512382829 | check (3.9) | ubuntu-latest | 2026-10-11T16:02:08Z | 2026-10-11T16:04:48Z | success | 160 |
| 38157611096 | 1 | 114523711590 | check (3.9) | ubuntu-latest | 2026-10-11T16:57:31Z | 2026-10-11T17:00:03Z | success | 152 |
| 38157611096 | 1 | 114523711757 | check (3.14) | ubuntu-latest | 2026-10-11T16:57:32Z | 2026-10-11T17:00:32Z | success | 180 |
| 38158003040 | 1 | 114524858624 | check (3.14) | ubuntu-latest | 2026-10-11T17:03:04Z | 2026-10-11T17:06:03Z | success | 179 |
| 38158003040 | 1 | 114524858844 | check (3.9) | ubuntu-latest | 2026-10-11T17:03:05Z | 2026-10-11T17:05:31Z | success | 146 |
| 38180591256 | 1 | 114592121447 | check (3.14) | ubuntu-latest | 2026-10-11T22:29:01Z | 2026-10-11T22:31:23Z | success | 142 |
| 38180591256 | 1 | 114592121702 | check (3.9) | ubuntu-latest | 2026-10-11T22:29:01Z | 2026-10-11T22:31:17Z | success | 136 |
| 38199694423 | 1 | 114650126050 | check (3.9) | ubuntu-latest | 2026-10-12T03:25:35Z | 2026-10-12T03:28:26Z | success | 171 |
| 38199694423 | 1 | 114650126219 | check (3.14) | ubuntu-latest | 2026-10-12T03:25:35Z | 2026-10-12T03:28:34Z | success | 179 |
| 38209410257 | 1 | 114679996252 | check (3.9) | ubuntu-latest | 2026-10-12T05:46:26Z | 2026-10-12T05:49:01Z | success | 155 |
| 38209410257 | 1 | 114679996298 | check (3.14) | ubuntu-latest | 2026-10-12T05:46:26Z | 2026-10-12T05:49:11Z | success | 165 |
| 38210318808 | 1 | 114682796052 | check (3.9) | ubuntu-latest | 2026-10-12T05:58:36Z | 2026-10-12T06:18:36Z | cancelled | 1200 |
| 38210318808 | 1 | 114682796177 | check (3.14) | ubuntu-latest | 2026-10-12T05:58:35Z | 2026-10-12T06:01:42Z | success | 187 |
| 38218481955 | 1 | 114708738051 | check (3.9) | ubuntu-latest | 2026-10-12T07:34:22Z | 2026-10-12T07:37:09Z | success | 167 |
| 38218481955 | 1 | 114708738248 | check (3.14) | ubuntu-latest | 2026-10-12T07:34:22Z | 2026-10-12T07:37:34Z | success | 192 |
| 38221849160 | 1 | 114719739898 | check (3.14) | ubuntu-latest | 2026-10-12T08:09:19Z | 2026-10-12T08:12:28Z | success | 189 |
| 38221849160 | 1 | 114719740106 | check (3.9) | ubuntu-latest | 2026-10-12T08:09:20Z | 2026-10-12T08:12:21Z | success | 181 |
| 38245293673 | 1 | 114797531012 | check (3.9) | ubuntu-latest | 2026-10-12T11:50:57Z | 2026-10-12T11:53:50Z | success | 173 |
| 38245293673 | 1 | 114797531330 | check (3.14) | ubuntu-latest | 2026-10-12T11:50:57Z | 2026-10-12T11:54:36Z | success | 219 |
| 38246547109 | 1 | 114801721943 | check (3.14) | ubuntu-latest | 2026-10-12T12:02:45Z | 2026-10-12T12:05:21Z | success | 156 |
| 38246547109 | 1 | 114801722137 | check (3.9) | ubuntu-latest | 2026-10-12T12:02:45Z | 2026-10-12T12:05:36Z | success | 171 |
| 38253155542 | 1 | 114824315938 | check (3.9) | ubuntu-latest | 2026-10-12T13:04:32Z | 2026-10-12T13:07:23Z | success | 171 |
| 38253155542 | 1 | 114824316150 | check (3.14) | ubuntu-latest | 2026-10-12T13:04:32Z | 2026-10-12T13:07:49Z | success | 197 |
| 38267989170 | 1 | 114874928570 | check (3.9) | ubuntu-latest | 2026-10-12T15:08:04Z | 2026-10-12T15:10:57Z | success | 173 |
| 38267989170 | 1 | 114874929078 | check (3.14) | ubuntu-latest | 2026-10-12T15:08:04Z | 2026-10-12T15:12:05Z | success | 241 |
| 38272206529 | 1 | 114888948883 | check (3.9) | ubuntu-latest | 2026-10-12T15:42:35Z | 2026-10-12T15:45:35Z | success | 180 |
| 38272206529 | 1 | 114888949126 | check (3.14) | ubuntu-latest | 2026-10-12T15:42:34Z | 2026-10-12T15:45:50Z | success | 196 |
| 38274490351 | 1 | 114896503433 | check (3.14) | ubuntu-latest | 2026-10-12T16:01:39Z | 2026-10-12T16:04:31Z | success | 172 |
| 38274490351 | 1 | 114896503726 | check (3.9) | ubuntu-latest | 2026-10-12T16:01:39Z | 2026-10-12T16:04:29Z | success | 170 |
| 38277738004 | 1 | 114907238054 | check (3.9) | ubuntu-latest | 2026-10-12T16:29:25Z | 2026-10-12T16:31:29Z | success | 124 |
| 38277738004 | 1 | 114907238072 | check (3.14) | ubuntu-latest | 2026-10-12T16:29:26Z | 2026-10-12T16:31:47Z | success | 141 |
| 38279956899 | 1 | 114914559259 | check (3.14) | ubuntu-latest | 2026-10-12T16:48:27Z | 2026-10-12T16:51:53Z | success | 206 |
| 38279956899 | 1 | 114914559631 | check (3.9) | ubuntu-latest | 2026-10-12T16:48:27Z | 2026-10-12T16:51:22Z | success | 175 |
| 38281000368 | 1 | 114917977140 | check (3.9) | ubuntu-latest | 2026-10-12T16:57:39Z | 2026-10-12T17:00:18Z | success | 159 |
| 38281000368 | 1 | 114917977359 | check (3.14) | ubuntu-latest | 2026-10-12T16:57:39Z | 2026-10-12T17:00:52Z | success | 193 |
| 38287371462 | 1 | 114938861426 | check (3.14) | ubuntu-latest | 2026-10-12T17:54:51Z | 2026-10-12T17:58:19Z | success | 208 |
| 38287371462 | 1 | 114938861678 | check (3.9) | ubuntu-latest | 2026-10-12T17:54:51Z | 2026-10-12T17:57:47Z | success | 176 |
| 38289742740 | 1 | 114946680092 | check (3.9) | ubuntu-latest | 2026-10-12T18:16:02Z | 2026-10-12T18:18:31Z | success | 149 |
| 38289742740 | 1 | 114946680362 | check (3.14) | ubuntu-latest | 2026-10-12T18:16:01Z | 2026-10-12T18:19:25Z | success | 204 |
| 38306539321 | 1 | 115001850817 | check (3.9) | ubuntu-latest | 2026-10-12T20:47:58Z | 2026-10-12T20:50:17Z | success | 139 |
| 38306539321 | 1 | 115001850999 | check (3.14) | ubuntu-latest | 2026-10-12T20:47:58Z | 2026-10-12T20:51:13Z | success | 195 |
| 38309800126 | 1 | 115012460613 | check (3.9) | ubuntu-latest | 2026-10-12T21:18:31Z | 2026-10-12T21:21:41Z | success | 190 |
| 38309800126 | 1 | 115012460868 | check (3.14) | ubuntu-latest | 2026-10-12T21:18:31Z | 2026-10-12T21:21:19Z | success | 168 |
| 38423526992 | 1 | 115386787409 | check (3.9) | ubuntu-latest | 2026-10-13T15:36:36Z | 2026-10-13T15:39:44Z | success | 188 |
| 38423526992 | 1 | 115386787848 | check (3.14) | ubuntu-latest | 2026-10-13T15:36:36Z | 2026-10-13T15:40:01Z | success | 205 |
| 38438557573 | 1 | 115438841713 | check (3.9) | ubuntu-latest | 2026-10-13T17:25:43Z | 2026-10-13T17:28:03Z | success | 140 |
| 38438557573 | 1 | 115438842202 | check (3.14) | ubuntu-latest | 2026-10-13T17:25:43Z | 2026-10-13T17:29:06Z | success | 203 |
| 38441600511 | 1 | 115449293386 | check (3.14) | ubuntu-latest | 2026-10-13T17:48:17Z | 2026-10-13T17:51:29Z | success | 192 |
| 38441600511 | 1 | 115449293455 | check (3.9) | ubuntu-latest | 2026-10-13T17:48:17Z | 2026-10-13T17:50:34Z | success | 137 |
| 38448199612 | 1 | 115472022598 | check (3.14) | ubuntu-latest | 2026-10-13T18:37:08Z | 2026-10-13T18:40:14Z | success | 186 |
| 38448199612 | 1 | 115472023279 | check (3.9) | ubuntu-latest | 2026-10-13T18:37:08Z | 2026-10-13T18:40:07Z | success | 179 |
| 38449831149 | 1 | 115477569816 | check (3.9) | ubuntu-latest | 2026-10-13T18:49:11Z | 2026-10-13T18:52:27Z | success | 196 |
| 38449831149 | 1 | 115477570091 | check (3.14) | ubuntu-latest | 2026-10-13T18:49:11Z | 2026-10-13T18:53:00Z | success | 229 |
| 38464881760 | 1 | 115528977117 | check (3.14) | ubuntu-latest | 2026-10-13T20:41:57Z | 2026-10-13T20:45:20Z | success | 203 |
| 38464881760 | 1 | 115528977389 | check (3.9) | ubuntu-latest | 2026-10-13T20:41:57Z | 2026-10-13T20:45:08Z | success | 191 |
| 38467244446 | 1 | 115537088174 | check (3.14) | ubuntu-latest | 2026-10-13T21:00:13Z | 2026-10-13T21:03:14Z | success | 181 |
| 38467244446 | 1 | 115537088464 | check (3.9) | ubuntu-latest | 2026-10-13T21:00:13Z | 2026-10-13T21:03:07Z | success | 174 |
| 38473047009 | 1 | 115556753567 | check (3.14) | ubuntu-latest | 2026-10-13T21:46:12Z | 2026-10-13T21:48:49Z | success | 157 |
| 38473047009 | 1 | 115556753763 | check (3.9) | ubuntu-latest | 2026-10-13T21:46:12Z | 2026-10-13T21:49:17Z | success | 185 |
| 38474148180 | 1 | 115560540903 | check (3.14) | ubuntu-latest | 2026-10-13T21:55:26Z | 2026-10-13T21:58:55Z | success | 209 |
| 38474148180 | 1 | 115560541252 | check (3.9) | ubuntu-latest | 2026-10-13T21:55:26Z | 2026-10-13T21:58:35Z | success | 189 |
| 38475645512 | 1 | 115565532411 | check (3.14) | ubuntu-latest | 2026-10-13T22:07:59Z | 2026-10-13T22:08:04Z | failure | 5 |
| 38475645512 | 1 | 115565532668 | check (3.9) | ubuntu-latest | 2026-10-13T22:07:59Z | 2026-10-13T22:08:06Z | failure | 7 |
| 38512141589 | 1 | 115682809706 | check (3.9) | ubuntu-latest | 2026-10-14T04:35:06Z | 2026-10-14T04:38:24Z | success | 198 |
| 38512141589 | 1 | 115682809879 | check (3.14) | ubuntu-latest | 2026-10-14T04:35:06Z | 2026-10-14T04:38:31Z | success | 205 |
| 38521001723 | 1 | 115710416476 | check (3.14) | ubuntu-latest | 2026-10-14T06:13:58Z | 2026-10-14T06:17:37Z | success | 219 |
| 38521001723 | 1 | 115710416692 | check (3.9) | ubuntu-latest | 2026-10-14T06:13:58Z | 2026-10-14T06:16:08Z | success | 130 |
| 38525138561 | 1 | 115723617244 | check (3.9) | ubuntu-latest | 2026-10-14T06:56:00Z | 2026-10-14T06:58:28Z | success | 148 |
| 38525138561 | 1 | 115723617340 | check (3.14) | ubuntu-latest | 2026-10-14T06:55:59Z | 2026-10-14T06:59:28Z | success | 209 |
| 38530073504 | 1 | 115739570976 | check (3.9) | ubuntu-latest | 2026-10-14T07:42:23Z | 2026-10-14T07:45:34Z | success | 191 |
| 38530073504 | 1 | 115739571269 | check (3.14) | ubuntu-latest | 2026-10-14T07:42:23Z | 2026-10-14T07:45:40Z | success | 197 |
| 38531941680 | 1 | 115745631973 | check (3.14) | ubuntu-latest | 2026-10-14T07:59:04Z | 2026-10-14T08:02:36Z | success | 212 |
| 38531941680 | 1 | 115745632395 | check (3.9) | ubuntu-latest | 2026-10-14T07:59:04Z | 2026-10-14T08:02:11Z | success | 187 |
| 38534116003 | 1 | 115752734693 | check (3.14) | ubuntu-latest | 2026-10-14T08:17:57Z | 2026-10-14T08:21:18Z | success | 201 |
| 38534116003 | 1 | 115752735082 | check (3.9) | ubuntu-latest | 2026-10-14T08:17:58Z | 2026-10-14T08:23:17Z | success | 319 |
| 38545074043 | 1 | 115788993846 | check (3.14) | ubuntu-latest | 2026-10-14T09:48:41Z | 2026-10-14T09:52:10Z | success | 209 |
| 38545074043 | 1 | 115788994368 | check (3.9) | ubuntu-latest | 2026-10-14T09:48:40Z | 2026-10-14T09:51:30Z | success | 170 |
| 38547994371 | 1 | 115798717906 | check (3.9) | ubuntu-latest | 2026-10-14T10:13:02Z | 2026-10-14T10:15:35Z | success | 153 |
| 38547994371 | 1 | 115798718247 | check (3.14) | ubuntu-latest | 2026-10-14T10:13:02Z | 2026-10-14T10:16:50Z | success | 228 |
| 38556615717 | 1 | 115827242883 | check (3.9) | ubuntu-latest | 2026-10-14T11:26:51Z | 2026-10-14T11:29:13Z | success | 142 |
| 38556615717 | 1 | 115827243570 | check (3.14) | ubuntu-latest | 2026-10-14T11:26:51Z | 2026-10-14T11:30:38Z | success | 227 |
| 38561729278 | 1 | 115844193188 | check (3.9) | ubuntu-latest | 2026-10-14T12:09:37Z | 2026-10-14T12:12:59Z | success | 202 |
| 38561729278 | 1 | 115844193412 | check (3.14) | ubuntu-latest | 2026-10-14T12:09:37Z | 2026-10-14T12:13:04Z | success | 207 |
| 38603255335 | 1 | 115987542660 | check (3.14) | ubuntu-latest | 2026-10-14T17:02:42Z | 2026-10-14T17:05:31Z | success | 169 |
| 38603255335 | 1 | 115987542701 | check (3.9) | ubuntu-latest | 2026-10-14T17:02:45Z | 2026-10-14T17:06:10Z | success | 205 |
| 38606890445 | 1 | 115999967551 | check (3.9) | ubuntu-latest | 2026-10-14T17:28:08Z | 2026-10-14T17:31:33Z | success | 205 |
| 38606890445 | 1 | 115999968116 | check (3.14) | ubuntu-latest | 2026-10-14T17:28:08Z | 2026-10-14T17:31:58Z | success | 230 |
| 38607227388 | 1 | 116001100807 | check (3.9) | ubuntu-latest | 2026-10-14T17:30:31Z | 2026-10-14T17:33:49Z | success | 198 |
| 38607227388 | 1 | 116001101061 | check (3.14) | ubuntu-latest | 2026-10-14T17:30:33Z | 2026-10-14T17:39:30Z | success | 537 |
| 38608025840 | 1 | 116003831630 | check (3.14) | ubuntu-latest | 2026-10-14T17:36:06Z | 2026-10-14T17:38:50Z | success | 164 |
| 38608025840 | 1 | 116003832272 | check (3.9) | ubuntu-latest | 2026-10-14T17:36:07Z | 2026-10-14T17:39:03Z | success | 176 |
| 38609513693 | 1 | 116008935299 | check (3.14) | ubuntu-latest | 2026-10-14T17:46:38Z | 2026-10-14T17:50:18Z | success | 220 |
| 38609513693 | 1 | 116008935818 | check (3.9) | ubuntu-latest | 2026-10-14T17:46:37Z | 2026-10-14T17:49:00Z | success | 143 |
| 38611201640 | 1 | 116014710632 | check (3.14) | ubuntu-latest | 2026-10-14T17:58:33Z | 2026-10-14T18:01:19Z | success | 166 |
| 38611201640 | 1 | 116014710988 | check (3.9) | ubuntu-latest | 2026-10-14T17:58:33Z | 2026-10-14T18:01:25Z | success | 172 |
| 38635628747 | 1 | 116097920237 | check (3.14) | ubuntu-latest | 2026-10-14T20:51:52Z | 2026-10-14T20:55:25Z | success | 213 |
| 38635628747 | 1 | 116097920648 | check (3.9) | ubuntu-latest | 2026-10-14T20:51:53Z | 2026-10-14T20:54:54Z | success | 181 |
| 38638182792 | 1 | 116106622189 | check (3.9) | ubuntu-latest | 2026-10-14T21:10:37Z | 2026-10-14T21:13:40Z | success | 183 |
| 38638182792 | 1 | 116106622675 | check (3.14) | ubuntu-latest | 2026-10-14T21:10:38Z | 2026-10-14T21:14:18Z | success | 220 |
| 38638688707 | 1 | 116108354636 | check (3.14) | ubuntu-latest | 2026-10-14T21:14:27Z | 2026-10-14T21:14:33Z | failure | 6 |
| 38638688707 | 1 | 116108354958 | check (3.9) | ubuntu-latest | 2026-10-14T21:14:27Z | 2026-10-14T21:14:32Z | failure | 5 |
| 38639165928 | 1 | 116109983563 | check (3.14) | ubuntu-latest | 2026-10-14T21:18:04Z | 2026-10-14T21:21:48Z | success | 224 |
| 38639165928 | 1 | 116109983811 | check (3.9) | ubuntu-latest | 2026-10-14T21:18:04Z | 2026-10-14T21:21:28Z | success | 204 |
| 38640853573 | 1 | 116115666914 | check (3.9) | ubuntu-latest | 2026-10-14T21:30:59Z | 2026-10-14T21:34:09Z | success | 190 |
| 38640853573 | 1 | 116115667203 | check (3.14) | ubuntu-latest | 2026-10-14T21:30:59Z | 2026-10-14T21:34:27Z | success | 208 |
| 38677838050 | 1 | 116235388654 | check (3.14) | ubuntu-latest | 2026-10-15T03:44:39Z | 2026-10-15T03:47:28Z | success | 169 |
| 38677838050 | 1 | 116235388786 | check (3.9) | ubuntu-latest | 2026-10-15T03:44:39Z | 2026-10-15T03:48:07Z | success | 208 |
| 38688774516 | 1 | 116269613594 | check (3.9) | ubuntu-latest | 2026-10-15T05:48:22Z | 2026-10-15T05:51:43Z | success | 201 |
| 38688774516 | 1 | 116269613902 | check (3.14) | ubuntu-latest | 2026-10-15T05:48:29Z | 2026-10-15T05:51:25Z | success | 176 |
| 38707624510 | 1 | 116330853949 | check (3.14) | ubuntu-latest | 2026-10-15T08:42:51Z | 2026-10-15T08:46:42Z | success | 231 |
| 38707624510 | 1 | 116330854348 | check (3.9) | ubuntu-latest | 2026-10-15T08:42:51Z | 2026-10-15T08:46:16Z | success | 205 |
| 38713915448 | 1 | 116351826880 | check (3.14) | ubuntu-latest | 2026-10-15T09:34:21Z | 2026-10-15T09:38:18Z | success | 237 |
| 38713915448 | 1 | 116351827153 | check (3.9) | ubuntu-latest | 2026-10-15T09:34:21Z | 2026-10-15T09:37:46Z | success | 205 |
| 38726054334 | 1 | 116392069937 | check (3.14) | ubuntu-latest | 2026-10-15T11:15:37Z | 2026-10-15T11:18:31Z | success | 174 |
| 38726054334 | 1 | 116392069983 | check (3.9) | ubuntu-latest | 2026-10-15T11:15:37Z | 2026-10-15T11:19:09Z | success | 212 |
| 38738877878 | 1 | 116435116283 | check (3.9) | ubuntu-latest | 2026-10-15T12:59:00Z | 2026-10-15T13:02:28Z | success | 208 |
| 38738877878 | 1 | 116435116662 | check (3.14) | ubuntu-latest | 2026-10-15T12:59:00Z | 2026-10-15T13:03:01Z | success | 241 |
| 38744466254 | 1 | 116454358930 | check (3.14) | ubuntu-latest | 2026-10-15T13:38:51Z | 2026-10-15T13:42:04Z | success | 193 |
| 38744466254 | 1 | 116454359395 | check (3.9) | ubuntu-latest | 2026-10-15T13:38:51Z | 2026-10-15T13:41:22Z | failure | 151 |
| 38745290285 | 1 | 116457221767 | check (3.14) | ubuntu-latest | 2026-10-15T13:44:36Z | 2026-10-15T13:48:34Z | success | 238 |
| 38745290285 | 1 | 116457222227 | check (3.9) | ubuntu-latest | 2026-10-15T13:44:36Z | 2026-10-15T13:47:07Z | success | 151 |
| 38759496913 | 1 | 116506693532 | check (3.9) | ubuntu-latest | 2026-10-15T15:20:50Z | 2026-10-15T15:24:24Z | failure | 214 |
| 38759496913 | 1 | 116506694213 | check (3.14) | ubuntu-latest | 2026-10-15T15:20:50Z | 2026-10-15T15:24:42Z | failure | 232 |
| 38760778015 | 1 | 116511164740 | check (3.14) | ubuntu-latest | 2026-10-15T15:29:31Z | 2026-10-15T15:33:16Z | success | 225 |
| 38760778015 | 1 | 116511165967 | check (3.9) | ubuntu-latest | 2026-10-15T15:29:31Z | 2026-10-15T15:32:41Z | success | 190 |
| 38762900881 | 1 | 116518548128 | check (3.9) | ubuntu-latest | 2026-10-15T15:43:59Z | 2026-10-15T15:47:29Z | success | 210 |
| 38762900881 | 1 | 116518548900 | check (3.14) | ubuntu-latest | 2026-10-15T15:43:59Z | 2026-10-15T15:48:04Z | success | 245 |
| 38763930750 | 1 | 116522099126 | check (3.9) | ubuntu-latest | 2026-10-15T15:51:04Z | 2026-10-15T15:53:45Z | success | 161 |
| 38763930750 | 1 | 116522099642 | check (3.14) | ubuntu-latest | 2026-10-15T15:51:03Z | 2026-10-15T15:54:48Z | success | 225 |
| 38768046178 | 1 | 116536309512 | check (3.9) | ubuntu-latest | 2026-10-15T16:19:41Z | 2026-10-15T16:23:13Z | success | 212 |
| 38768046178 | 1 | 116536309925 | check (3.14) | ubuntu-latest | 2026-10-15T16:19:41Z | 2026-10-15T16:24:19Z | success | 278 |
| 38772118466 | 1 | 116550342791 | check (3.14) | ubuntu-latest | 2026-10-15T16:48:57Z | 2026-10-15T16:53:51Z | success | 294 |
| 38772118466 | 1 | 116550343110 | check (3.9) | ubuntu-latest | 2026-10-15T16:48:56Z | 2026-10-15T16:51:23Z | success | 147 |
| 38780829803 | 1 | 116580279775 | check (3.14) | ubuntu-latest | 2026-10-15T17:51:44Z | 2026-10-15T17:55:23Z | success | 219 |
| 38780829803 | 1 | 116580280412 | check (3.9) | ubuntu-latest | 2026-10-15T17:51:44Z | 2026-10-15T17:55:11Z | success | 207 |
| 38786307663 | 1 | 116599065485 | check (3.14) | ubuntu-latest | 2026-10-15T18:31:20Z | 2026-10-15T18:35:16Z | success | 236 |
| 38786307663 | 1 | 116599065955 | check (3.9) | ubuntu-latest | 2026-10-15T18:31:20Z | 2026-10-15T18:34:57Z | success | 217 |
| 38788519620 | 1 | 116606689873 | check (3.14) | ubuntu-latest | 2026-10-15T18:47:24Z | 2026-10-15T18:51:50Z | success | 266 |
| 38788519620 | 1 | 116606690215 | check (3.9) | ubuntu-latest | 2026-10-15T18:47:24Z | 2026-10-15T18:50:56Z | success | 212 |
| 38840614300 | 1 | 116779593756 | check (3.9) | ubuntu-latest | 2026-10-16T02:28:39Z | 2026-10-16T02:31:30Z | success | 171 |
| 38840614300 | 1 | 116779593974 | check (3.14) | ubuntu-latest | 2026-10-16T02:28:39Z | 2026-10-16T02:32:33Z | success | 234 |
| 38852907300 | 1 | 116817875642 | check (3.14) | ubuntu-latest | 2026-10-16T04:53:15Z | 2026-10-16T04:57:12Z | success | 237 |
| 38852907300 | 1 | 116817875905 | check (3.9) | ubuntu-latest | 2026-10-16T04:53:15Z | 2026-10-16T04:55:51Z | success | 156 |
| 38904702584 | 1 | 116985238791 | check (3.9) | ubuntu-latest | 2026-10-16T13:01:29Z | 2026-10-16T13:04:02Z | success | 153 |
| 38904702584 | 1 | 116985239303 | check (3.14) | ubuntu-latest | 2026-10-16T13:01:29Z | 2026-10-16T13:05:38Z | success | 249 |
| 38906508369 | 1 | 116991299915 | check (3.14) | ubuntu-latest | 2026-10-16T13:15:50Z | 2026-10-16T13:19:42Z | success | 232 |
| 38906508369 | 1 | 116991300456 | check (3.9) | ubuntu-latest | 2026-10-16T13:15:51Z | 2026-10-16T13:19:35Z | success | 224 |
| 38915460898 | 1 | 117021654811 | check (3.9) | ubuntu-latest | 2026-10-16T14:24:10Z | 2026-10-16T14:28:06Z | success | 236 |
| 38915460898 | 1 | 117021655323 | check (3.14) | ubuntu-latest | 2026-10-16T14:24:10Z | 2026-10-16T14:28:14Z | success | 244 |
| 38921008623 | 1 | 117040604245 | check (3.9) | ubuntu-latest | 2026-10-16T15:04:58Z | 2026-10-16T15:08:52Z | success | 234 |
| 38921008623 | 1 | 117040604658 | check (3.14) | ubuntu-latest | 2026-10-16T15:04:58Z | 2026-10-16T15:09:22Z | success | 264 |
| 38929856416 | 1 | 117070751159 | check (3.9) | ubuntu-latest | 2026-10-16T16:10:39Z | 2026-10-16T16:13:49Z | success | 190 |
| 38929856416 | 1 | 117070751300 | check (3.14) | ubuntu-latest | 2026-10-16T16:10:39Z | 2026-10-16T16:14:48Z | success | 249 |
| 38931195385 | 1 | 117075304856 | check (3.14) | ubuntu-latest | 2026-10-16T16:20:41Z | 2026-10-16T16:25:08Z | success | 267 |
| 38931195385 | 1 | 117075305502 | check (3.9) | ubuntu-latest | 2026-10-16T16:20:41Z | 2026-10-16T16:24:28Z | success | 227 |
| 38953809734 | 1 | 117151510012 | check (3.9) | ubuntu-latest | 2026-10-16T19:17:26Z | 2026-10-16T19:21:19Z | success | 233 |
| 38953809734 | 1 | 117151510445 | check (3.14) | ubuntu-latest | 2026-10-16T19:17:26Z | 2026-10-16T19:21:36Z | success | 250 |
| 38955292748 | 1 | 117156518111 | check (3.14) | ubuntu-latest | 2026-10-16T19:29:30Z | 2026-10-16T19:33:41Z | success | 251 |
| 38955292748 | 1 | 117156518276 | check (3.9) | ubuntu-latest | 2026-10-16T19:29:30Z | 2026-10-16T19:33:27Z | success | 237 |
| 38974313605 | 1 | 117219948717 | check (3.14) | ubuntu-latest | 2026-10-16T22:12:17Z | 2026-10-16T22:16:03Z | success | 226 |
| 38974313605 | 1 | 117219949042 | check (3.9) | ubuntu-latest | 2026-10-16T22:12:17Z | 2026-10-16T22:16:12Z | success | 235 |
| 38975543979 | 1 | 117223981190 | check (3.9) | ubuntu-latest | 2026-10-16T22:24:22Z | 2026-10-16T22:27:51Z | success | 209 |
| 38975543979 | 1 | 117223981430 | check (3.14) | ubuntu-latest | 2026-10-16T22:24:22Z | 2026-10-16T22:28:53Z | success | 271 |
| 38986578072 | 1 | 117259347661 | check (3.9) | ubuntu-latest | 2026-10-17T00:29:11Z | 2026-10-17T00:33:13Z | success | 242 |
| 38986578072 | 1 | 117259347943 | check (3.14) | ubuntu-latest | 2026-10-17T00:29:11Z | 2026-10-17T00:32:55Z | success | 224 |
| 38986751555 | 1 | 117259895166 | check (3.9) | ubuntu-latest | 2026-10-17T00:31:21Z | 2026-10-17T00:35:11Z | success | 230 |
| 38986751555 | 1 | 117259895569 | check (3.14) | ubuntu-latest | 2026-10-17T00:31:22Z | 2026-10-17T00:35:24Z | success | 242 |
| 38987176840 | 1 | 117261247138 | check (3.9) | ubuntu-latest | 2026-10-17T00:36:55Z | 2026-10-17T00:39:24Z | success | 149 |
| 38987176840 | 1 | 117261247458 | check (3.14) | ubuntu-latest | 2026-10-17T00:36:55Z | 2026-10-17T00:40:57Z | success | 242 |
