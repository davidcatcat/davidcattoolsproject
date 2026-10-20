# OpenCode Developer status reconciliation — 2026-10-20

**OBSERVED evidence record; not acceptance, not a work-package completion, not a CLI wake.**
Scope: A11c foreground delivery chain, PR [#223](https://github.com/davidcatcat/davidcattoolsproject/pull/223), PR [#224](https://github.com/davidcatcat/davidcattoolsproject/pull/224), and the `RETAIN_BLOCKED` OpenCode clock-fix package. No source file, test, workflow, skill or policy text is changed by this report. It contains no secret, credential, token or private-filesystem inventory.

This report is the durable copy of three GitHub records published at `2026-10-20T02:24Z`:

- [Issue #120 status reconciliation](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6281874306)
- [PR #224 exact-head CI](https://github.com/davidcatcat/davidcattoolsproject/pull/224#issuecomment-6281874650)
- [PR #223 M1 delta note](https://github.com/davidcatcat/davidcattoolsproject/pull/223#issuecomment-6281874968)

## Provenance

- Canonical Mac checkout: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` (Git common-dir `.git` of the same root).
- Canonical local `main` at observation time: `1a16fea` — **13 commits behind** `origin/main` `63da8b84beb5590b04bf12bcbfa3d6e291f414c8`. No fetch-and-sync, no checkout switch and no rebase were performed by this task.
- Report worktree (D6): `tasks/opencode-status-reconciliation-20261020`, branch `docs/opencode-status-reconciliation-20261020`, base `63da8b84beb5590b04bf12bcbfa3d6e291f414c8`, locked, docs-only.
- Data sources: `git rev-parse`/`git log`/`git diff`/`git show` against local refs and `origin/main`; `gh pr view`, `gh pr checks`, `gh api` for PR state, CI runs, comments and issue events. Read-only throughout.
- Observation window: `2026-10-20T02:20Z`–`2026-10-20T02:30Z`. All SHAs and statuses below are as of that window; CI and PR state can change afterwards.

## 1. PR #224 — exact-head CI PASS, merge gate pending

| Field | Value |
|---|---|
| Head | `0ba02d0068b1d29a9fd1d8288b87b577a2a734cc` |
| Base | `63da8b84beb5590b04bf12bcbfa3d6e291f414c8` |
| Mergeable | `MERGEABLE` / `CLEAN` |
| Diff | 2 files, +191 |
| CI run | [39358209439](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/39358209439) — `success` |

Exact-head jobs: `check (3.9)` pass 3m6s, `check (3.14)` pass 4m53s. Run `head_sha` equals `headRefOid` equals the branch head `0ba02d0`, so the run is exact evidence for that SHA and not for GitHub's temporary merge commit.

Changed paths: `tools/llmtool/llmtool/opencode_foreground_host.py`, `tools/llmtool/tests/test_opencode_foreground_host.py`.

**Not established:** a live OpenCode launch, any D4 credential or admission path, any Worker inference. The PR body's "offline-only, no provider call, no credential read" statement is a Developer claim; only the diff and CI are independently checkable from this report. Merge decision belongs to the Integrator.

## 2. PR #223 — M1 (Medium) was repaired before merge

Claude's formal M1 finding ([comment](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6280936838), `2026-10-20T01:09Z`) reviewed head `98c37e719cdec76046070a23b6358d9ba49d9d93`. That is **not** the merged head:

| Item | Value |
|---|---|
| Reviewed head | `98c37e719cdec76046070a23b6358d9ba49d9d93` |
| Merged head | `8e305afc36321cda07a8428d846a98191bfc30b0` — `fix(llmtool): reject malformed active-session intake fields` (2026-10-20 09:30 +08:00) |
| Merge commit | `63da8b84beb5590b04bf12bcbfa3d6e291f414c8` at `2026-10-20T01:35:49Z` |
| Delta size | `git diff 98c37e71 8e305afc` = 2 files, +68/−16 |

M1 items present in the merged source `tools/llmtool/llmtool/developer_session_intake.py`:

- `type(r.state) is not str` added to the ACTIVE check → `BLOCKED/INTAKE_SESSION_UNAVAILABLE`
- `_hex(r.source_sha, _HEX40)` and `_hex(o.source_sha, _HEX40)` → `BLOCKED/INTAKE_SESSION_MISMATCH`
- `type(r.prior_task_id) is not str or not r.prior_task_id` and `_hex(r.prior_job_digest, _HEX64)` → `BLOCKED/INTAKE_PREVIOUS_NOT_TERMINAL`
- `not _session(previous.integrator_session)` → `BLOCKED/INTAKE_PREVIOUS_NOT_TERMINAL`
- packet construction wrapped in `try/except (TypeError, ValueError, UnicodeError)` → `BLOCKED/INTAKE_PACKET_INVALID`, which covers the `TypeError`-instead-of-`BLOCKED` case in M1's reproducer

New test `test_malformed_registration_and_previous_fields_fail_closed` asserts `BLOCKED` for 12 malformed inputs — `Liar` (a `str` subclass overriding `__eq__`/`__ne__`) and `Explosive` (raises on comparison) applied to `state`, `prior_task_id`, `prior_job_digest`, `previous.integrator_session`, and `source_sha` on both `registration` and `observation`.

Merge integrity check: `git diff 8e305afc:tools/llmtool/llmtool/developer_session_intake.py origin/main:tools/llmtool/llmtool/developer_session_intake.py` is empty, so the repair was not lost in the merge commit.

**Not executed for this report.** No test run backs the above, so no PASS is claimed. The suggested targeted re-review scope is exactly `98c37e71..8e305afc` at `63da8b84`, executing:

```sh
python -m pytest tools/llmtool/tests/test_developer_session_intake.py -q
```

Low items L1–L3 from the original review are untouched by this delta and remain open as documented. `run_live()` still refuses unconditionally and no live authority is claimed by the module.

## 3. OpenCode clock-fix package remains `RETAIN_BLOCKED`

Unchanged by this report. The earlier interactive OpenCode assignment (read-only analysis of the real-clock `REVIEW_OBSERVATION_STALE` D1/D2 finding) was interrupted and retained after the session's effective tools were not confined to the admitted four-file scope. Recovery requires a Host-enforced file boundary; prompt-level restrictions are insufficient.

- No resume, no retry, no model call from that attempt was made here.
- Retained evidence: 19 model turns, 54,465 input, 3,223 output, 13,560 reasoning, 2,026,496 cached-read tokens, provider-reported cost 0; external billing `UNKNOWN`.
- Task worktree `tasks/a11c-review-live-clock-opencode-20261020` stays `RETAIN_BLOCKED` with its recorded disposition; this report did not read, modify or retire it.

## 4. Reviewer channel limitation

Claude's `2026-10-20T02:18:23Z` summary reports its native replies fail with `ENOENT` because the dispatching relay sessions had already exited, and that GitHub is the only reliable channel. Consequences:

- GitHub comments and this report are durable **records**, not delivery, wake or ACK.
- Any handoff must be re-read by the actual Integrator later; nothing here proves notification.

## 5. What this task did and did not do

- No source, test, workflow, skill or policy edits beyond adding this one report file.
- No llmtool Host/Worker launch, no llmtool provider call, no credential or environment dump.
- No Git history rewrite, no force push, no branch deletion, no peer worktree touched.
- One D6 docs worktree created and locked; the canonical checkout was not mutated and remains on local `main` at `1a16fea`.
- Session/token usage for this report: `UNKNOWN` (no llmtool accounting record was produced). The report aggregates previous evidence; it does not re-measure it.

## 6. Reproducible checks used

```sh
git -C /Users/davidsun/git/davidcataiporject/davidcattoolsproject rev-parse origin/main
gh pr view 224 --json headRefOid,baseRefOid,mergeable,mergeStateStatus
gh pr checks 224
gh api repos/davidcatcat/davidcattoolsproject/actions/runs/39358209439 \
  --jq '"run \(.id) \(.status)/\(.conclusion) head=\(.head_sha)"'
git diff 98c37e71 8e305afc --stat
git diff 8e305afc:tools/llmtool/llmtool/developer_session_intake.py \
         origin/main:tools/llmtool/llmtool/developer_session_intake.py
```

## 7. Open decisions (Integrator/PM)

1. Dispose of PR #224 at the merge gate, or request a further bounded revision.
2. Decide whether to scope a targeted Claude re-review of `98c37e71..8e305afc` for PR #223, and who executes the delta tests.
3. Issue (or withhold) the next bounded Task Contract. Any source package for OpenCode requires the Host-enforced file boundary first; this report does not create that boundary, a model router, a budget increase or a native admission.
