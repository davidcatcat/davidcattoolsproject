# llmtool A11b.2 Git Hardening — targeted spec re-review handoff (2026-10-07)

## Purpose

Targeted follow-up review only. The full spec was already independently reviewed at
`2fc0f944803a23c3ed63fb1f3905b08a8148f6ce` and returned BLOCKED on B1–B4, with
B5 deferred to A11b.2.2.

Current remediated spec head:

`29d0b6aba1e41f301f3ad15520afbe87c496ddec`

Review:
- `reports/llmtool_a11b2_git_hardening_spec_review_20261007.md`

Spec:
- `reports/llmtool_a11b2_git_hardening_implementation_spec_20261007.md`

Do not implement code. Do not modify the spec.
Do not review unrelated A11b.2.2 process-containment details beyond confirming B5 is still correctly gated.

## Verify B1

Confirm mutation now:
- rejects any configured filter driver rather than neutralizing it;
- rejects target `filter` attribute even with no configured driver;
- keeps A5 read-only neutralization conceptually separate;
- prevents git-crypt/LFS raw staging risk.

## Verify B2

Confirm:
- target transforming attributes (`filter`, `text`, `eol`, `crlf`, `ident`, `working-tree-encoding`) are rejected unless unspecified/unset;
- `core.autocrlf=false`, `core.safecrlf=false`, `add.ignoreErrors=false` pinned;
- `core.fileMode=false` rejected;
- post-verify computes staged blob OID from exact A11b.1-bound bytes using repo object format and checks mode.

## Verify B3

Confirm spec now makes a hard decision:
- canonical root realpath exact + no-follow fd identity;
- real `.git` directory only;
- explicit GIT_DIR/GIT_WORK_TREE;
- no gitfile/commondir/alternates/core.worktree/config includes;
- ordinary `<root>/.git/index` only;
- missing index reject;
- index.lock reject / never delete;
- split/shared/sparse index reject;
- skip-worktree/assume-unchanged/intent-to-add reject;
- rename detection disabled for mutation preflight.

## Verify B4

Confirm:
- semantic index-entry comparison replaces raw-index byte equality;
- exact path bytes/mode/OID/stage/flags are authoritative;
- unrelated stat/cache refresh may change raw index SHA but not semantic entry;
- target path byte identity is exact.

## Verify R5–R10

Confirm the spec/test matrix now records:
- production import/CLI boundary test;
- primitive does not read ApprovalValidationResult;
- audit pre/post/typed-failure fields;
- concrete adversarial tests from prior review;
- same-UID malicious process is outside preventable isolation boundary;
- OS sandbox not required for this ADD-only subset only if repository-code execution vectors are proven rejected/disabled.

## B5

B5 remains an A11b.2.2 blocker only.
A11b.2.1 is allowed to start if B1–B4 and R5–R10 are closed.

## Required verdict

Create:
`reports/llmtool_a11b2_git_hardening_spec_targeted_rereview_20261007.md`

Finish with:

```text
A11b.2 TARGETED SPEC RE-REVIEW:
PASS
or
BLOCKED

Exact review head:
29d0b6aba1e41f301f3ad15520afbe87c496ddec

B1 filter mutation rule:
CLOSED / BLOCKED

B2 attributes/blob verification:
CLOSED / BLOCKED

B3 repository/index subset:
CLOSED / BLOCKED

B4 semantic post-verify:
CLOSED / BLOCKED

R5–R10:
CLOSED / finding

B5 status:
DEFERRED TO A11b.2.2 / finding

New A11b.2.1 blockers:
NONE / details

May implement A11b.2.1:
YES / NO
```

Commit/push only the targeted re-review report, then STOP.