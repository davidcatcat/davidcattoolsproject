# llmtool A11b.2.1 — coordinator remediation re-review (2026-10-07)

## Reviewed revision

PR #43

Remediation implementation head:
`a000e003298cd8ff9bd26f0d8a3892cd9882a11d`

Publication head:
`fc11875b45b07aab94f12a0aa9b1da7df3ced3a3`

The delta from remediation implementation head to publication head is report-only.
No production or test bytes changed in that delta.

Final-head GitHub Actions:
- PR workflow 37530812194: SUCCESS
- push workflow 37530804250: SUCCESS

## F1 disposition

**CLOSED.**

The remediation now:
- rejects repository-local `core.excludesFile` case-insensitively as `unsafe config`;
- pins `core.excludesFile=/dev/null` in the fixed Git config used by every hardened read and the future ADD argv;
- rejects before `_Read.IGNORE` can consume the configured external path;
- preserves normal repository `.gitignore` and `.git/info/exclude` behavior;
- adds explicit external-path, mixed-case, FIFO non-following, pin-presence and info/exclude regression coverage.

The FIFO test does not rely on timeout as its security proof: it asserts that only the no-includes config query is spawned before rejection.

## Regression evidence

Submitted remediation validation records:
- targeted `test_git_mutation.py`: 120 discovered, 118 PASS, 2 existing host skips;
- A5 `test_git_intel.py`: 39 discovered, 38 PASS, 1 existing host skip;
- A11b.1 `test_action_contract.py`: 21/21 PASS;
- full Python 3.13.3: 1,157 discovered, 1,154 PASS, 3 existing skips;
- full Python 3.9.6: 1,157 discovered, 1,154 PASS, 3 existing skips;
- `git diff --check`: PASS.

Final publication-head CI independently passed on Python 3.9/3.14 according to the project workflow.

## Boundary review

The F1 remediation did not widen the slice:
- no `git add` execution;
- no public CLI/runtime mutation path;
- no arbitrary Git argv;
- no Action/approval schema change;
- no A11b.2.2 code;
- B5 remains open and blocking A11b.2.2.

## Verdict

```text
A11b.2.1 COORDINATOR RE-REVIEW:
PASS

Reviewed implementation head:
a000e003298cd8ff9bd26f0d8a3892cd9882a11d

Publication head:
fc11875b45b07aab94f12a0aa9b1da7df3ced3a3

F1 core.excludesFile:
CLOSED

External-path rejection:
PASS

FIFO non-following:
PASS

Fixed /dev/null pin:
PASS

.gitignore regression:
PASS

.git/info/exclude regression:
PASS

Targeted/A5/A11b.1/full regression evidence:
PASS

Final-head CI:
PASS

git add execution:
NOT IMPLEMENTED

Public mutation path:
NONE

A11b.2.2:
NOT AUTHORIZED

Ready for independent code/security review:
YES

Ready to merge:
NO — independent review still required
```