# ai-loop QA Low controller — independent review and recommendation (2026-10-09)

Evidence label: **ordinary review** (`review-change`) of the `automation/ai-loop`
controller plus a reading of the local PR #55 QA record. One Mac test run is
included (below). This is not a security-review, QA or merge gate.

Reviewer: Claude Code (Opus 5.5), on the Mac canonical repo, at the operator's request.

## 1. What `run.sh` actually does

`.git/ai-loop/run.sh` is a launcher. On every run it fetches
`automation/ai-loop`, takes `scripts/ai_loop.py` (plus helpers) from that branch
tip and runs it with `python3`. Whatever is on that branch when the operator types
the command is what runs on the Mac, so the code is new on every run.

- PRs #57–#60 were opened and merged into `automation/ai-loop` within about 20
  minutes, under the same account, with no human review.
- The repository is private. The current GitHub plan does not offer branch protection
  (API 403), so nothing technical limits who can change what the Mac runs.
- `scripts/ai_loop.py` is 4467 lines. It contains many one-off hard-coded routes
  (run ID `20261009T050558Z-60868`, Issue 56, PR 55).

## 2. What `--qa-low-review-diagnose 20261009T050558Z-60868` did

Source at control SHA `d127ab6`:

1. It read the local private record
   `.ai-loop/qa-low-disposition-20261009T050558Z-60868/code-review.raw.json`.
   That record is the Claude code review of the **controller's own QA Low scripts**,
   not the product code. The review was **FAIL with 11 findings**.
2. It called `claude -p` read-only to map each finding to fixed values (category,
   area, action, confidence).
3. It posted only SHA256 hashes and those values to Issue #56. It changed no code
   and merged nothing.

The posted table does not include the finding text. The coordinator therefore still
cannot act on it, which would lead to yet another round of scripts.

## 3. The 11 controller review findings — verification status

Reviewed source: `3911e81`. The current controller `d127ab6` changed
`ai_loop_qa_low_disposition.py` by only 15 lines, so the findings still apply.

| # | Finding (summary) | Status |
| --- | --- | --- |
| 1 | The positive-path inventory test fails on macOS because `/var` → `/private/var` trips the parent-symlink rejection | **CONFIRMED on this Mac** (see below) |
| 2 | The bootstrap blob check covers only `ai_loop_qa_low_disposition.py`, not the imported inventory module | Confirmed by reading `verify_bootstrap_blob` (only one path is checked). Partly mitigated: the launcher writes both files from `git show` and compares them on collision. |
| 3 | The integrity check runs after `check_state()` has already used `gh`/`git fetch` | Confirmed by reading (`code_review()` is called after `check_state()`) |
| 4 | It automatically POSTs to Issue #56 without a human confirmation step, which contradicts the "no publication" docstring | Confirmed by reading |
| 5 | It sends private QA details to `claude -p`; the record has `product_provider_egress_authorized: False` | Needs an operator decision. Claude on the operator's own machine is not the company provider, but the docstrings claim otherwise. |
| 6 | The publication marker is written after the POST with `write_text` (not exclusive) | Confirmed by reading (still present at `d127ab6`) |
| 7 | The cache files are trusted on reuse | Partly fixed in `d127ab6` (revision-keyed). Acceptable for a single-user Mac. |
| 8 | The "read-only" docstring is inaccurate (it runs `git fetch` and writes state) | Confirmed; documentation only |
| 9 | TOCTOU: `qa-pr55.json` is read three times | Plausible; low impact |
| 10 | Some exceptions print a traceback instead of reaching the STOP path | Plausible; still fails closed |
| 11 | The reviewer could not recompute the frozen SHA | Reviewer limitation, not a defect |

Mac test evidence for #1 (`d127ab6`, `python3 -m unittest test_ai_loop_qa_low_inventory`,
default `TMPDIR=/var/folders/...`):

```text
test_six_findings_are_metadata_only ... ERROR
ai_loop_qa_low_inventory.EvidenceError: symlink in QA evidence path
Ran 10 tests ... FAILED (errors=1)
```

The controller CI runs on Linux, which is why it never caught this.

**Conclusion:** the controller FAIL is legitimate. However, the controller only exists
to sort six **non-blocking Low** findings. Fixing all of the above would cost more than
the decision it supports.

## 4. The real decision: PR #55 and its six Low findings

From the local record `.ai-loop/20261009T050558Z-60868/qa-pr55.json` at PR #55 HEAD
`ee8180bc3ad5c455b63e493fee9f2955a90c5463`:

- Security role: **PASS**. Functional QA role: **PASS**. Related and full local tests: PASS.
- PR #55 CI: Python 3.9 and 3.14 checks pass.
- All six findings are `severity: Low` and `blocking: False`.

| Role | File | Finding (summary) | Recommended disposition |
| --- | --- | --- | --- |
| security | `test_output_excerpts.py` | The sensitive-pattern scan runs on the full capture, not on each truncated excerpt. A `\b` boundary can appear at the cut. Not exploitable while excerpts stay local. | DEFER: rescan each excerpt in the future redaction/egress gate before any excerpt leaves the machine |
| security | `test_test_output_excerpts.py` | A fixed 5 s child-process deadline may flake on a loaded Python 3.9 runner | ACCEPT (watch CI); raise the limit only if it flakes |
| functional_qa | `test_output_excerpts.py` | Benign output (`Basic ...`, `token = ...`, `ERROR:root:` logs) can cause rejection or INCOMPLETE. It fails closed and is documented. | DEFER: measure the false-rejection rate in the C1b benchmark |
| functional_qa | `cli.py` | Exit codes in two edge cases (non-EPIPE OSError → 1, argparse error → 2) differ from the README wording | ACCEPT, or do a one-line README fix in a follow-up |
| functional_qa | tests | Evidence gaps: pytest fixtures are synthetic, closed-pipe behaviour is mocked, near-limit JWT cases are ASCII only | DEFER: test-hardening follow-up |
| functional_qa | tests | About 30 near-1 MiB children share a 5 s timeout (same issue as security #2) | ACCEPT (same as above) |

None of the six findings needs a code change before merge.

## 5. Recommendation

1. **Stop repairing the QA Low controller** (`--qa-low`, `--qa-low-review-diagnose`).
   Freeze it as it is. Do not open PR #61 for it.
2. The **operator** decides on PR #55 using section 4. A model-generated table is not
   a merge authorization. If accepted, record the dispositions on Issue #56 and open
   one follow-up issue for the DEFER items.
3. Going forward, the coordinator should get readable finding text, or a human
   summary like this one, rather than hash-only tables.
4. If the launcher stays, consider pinning `run.sh` to an operator-approved SHA instead
   of the branch tip, so that each new script is reviewed before it runs.
