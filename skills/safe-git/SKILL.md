---
name: safe-git
description: Safely inspect, synchronize, commit, and push Git changes. Use when the user asks to fetch, pull, sync, commit, push, update GitHub, or publish repository changes.
---

# Safe Git

Treat remote history and unrelated local work as protected state.

## Owner-approved two-agent developer worktree exception (NOT ACTIVATED)

The requirements owner explicitly approved **at most two fixed, reviewed linked Git
worktrees** for trusted Codex CLI and Claude CLI development in
[Issue #110](https://github.com/davidcatcat/davidcattoolsproject/issues/110#issuecomment-6106421018).
This is a narrowly scoped exception to the former canonical-only prohibition,
**not** general permission to create project checkouts, clones, Git archive copies
or arbitrary temporary worktrees.

**Operational mode: DISABLED for general parallel development.** Owner approval
and a merged documentation PR are NOT permission to provision or use worktrees.
A pre-activation **limited Mac pilot** is a separate exception, narrowly gated
below; it does not change this general DISABLED mode. Until such a pilot is
independently authorized, use the original single canonical writer and
the claim/release handoff below.

**Formal activation record:** After the #115 pilot passes all required Mac,
independent-review and canonical-preservation gates, a separately reviewed and
merged **activation-status documentation PR** must change the operational mode
here to ACTIVE and cite the exact #115 native evidence, approved roots and
policy/source SHAs. Until that PR merges, normal two-agent development remains
DISABLED. Keep this section's heading/anchor stable when changing the status,
so the existing policy links remain valid.

Canonical integrator and required native acceptance root (must never be moved
or cleaned as part of allocation):
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

The **only** eligible fixed developer roots, during an explicitly authorized
#115 limited pilot or after formal activation:
- Codex CLI: `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`
- Claude CLI: `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude`

### Immediate single-checkout handoff (active now)

Before branch/source/Git writes in the shared canonical root, verify its exact
root, branch, HEAD, remote, tracked/untracked state and whether another agent's
test/child process is using it. The writer posts
`CHECKOUT-CLAIM <agent> <task-branch> <observed-HEAD>` in the task's GitHub
handoff. A PR publication or review request is **not** a release. When source
is published and all owned tests/descendants have stopped, return safely to
`main` only if clean, verify tracked/untracked data and HEAD, then post
`CHECKOUT-RELEASE <agent> main <HEAD>`. If unsafe, post HOLD with evidence
without switching/cleaning. Audit comments are **not atomic locks**: the next
agent must independently verify exclusive local ownership and fresh state.
Never infer current local ownership from a historical comment or bare PID.

### Pre-activation limited Mac pilot (NOT general activation)

Before any real-project worktree is created, the #115 native plan must be
independently Security/Functional reviewed at exact source/command revision.
A coordinator's separate, explicit **PILOT_SCOPE_AUTHORIZED** record in #115
must identify the named Mac executor, approved immutable policy SHA, exact
two fixed paths, owned task branches, allowed preflight/provision/test commands
and stop/retention criteria. A generic issue comment, review request, expired
CLAIM/RELEASE, or this docs change is NOT pilot authority. The executor must
independently confirm the current canonical checkout, no other writer or
descendants, owner/permissions, no symlink/path escape, Git common-dir/branch
inventory and preservation of all unknown data. If any identity, plan or
permission differs, STOP without provisioning or changing any Git state.

**Only within that approved pilot scope**, its named executor may provision
and test the two exact linked worktrees while normal developer parallel use
remains DISABLED. The pilot may run bounded overlapping **test/fixture-only
or otherwise separately approved small development tasks**, without touching
canonical source/user data or converting the pilot into unrestricted feature
development. The pilot never authorizes llmtool F2 model-initiated mutation.
A verified fail-closed test or recovered interrupted session is evidence to
review, not an automatic activation. The coordinator must obtain an
independent native gate verdict before any ACTIVE-status PR.

### Controls for both the limited pilot and formally activated development

- Only the named, approved integrator/provisioner may stage a linked worktree at
  one of the two exact, empty, verified roots. Check parent ownership, 0700
  permissions, symlink/ACL risks, canonical common-dir identity and existing
  Git worktree/branch inventory first. Preexisting data or ambiguous identity
  means STOP/preserve; no overwrite, auto-adopt, prune or delete.
- The canonical checkout keeps local `main`. **Neither agent worktree may
  check out `main`**, another agent's task branch or `automation/ai-loop`.
  Each agent owns one worktree and one unique task branch at a time; verify
  root, common-dir, HEAD, branch, staged/unstaged/untracked/ignored contents
  and allowlisted files before every mutation. No concurrent writers in a
  single worktree; do not switch a root underneath running tests.
- Each developer worktree needs its **own venv, editable install, build/cache
  and test state**, not a symlink to the canonical venv. Record
  `sys.executable`, `llmtool.__file__`, root and exact source SHA; if
  imports resolve to canonical or a peer worktree, STOP and invalidate that
  test evidence. Do not assume `-I` protects a shared editable install.
- Confine stage/commit to explicit allowed paths, push only one's owned ref
  (no force), publish Draft PR and freeze exact HEAD for review. An independent
  reviewer cannot self-approve authored changes. Shared worktree provisioning,
  ref integrations, destructive Git maintenance and canonical Mac native
  acceptance stay serialized with the integrator. Bounded ordinary
  `git fetch origin` is permitted within an authorized worktree subject to
  known-safe Git environment/config and conflict/lock handling.
- No `reset --hard`, `clean`, arbitrary stash, forced checkout/push,
  `gc`, `prune`, `worktree remove/move/repair`, shared config mutation,
  unattended process kill or automatic workspace cleanup. Preserve dirty,
  ignored and unknown user data, commits and active descendants on crash or
  failed validation. Fall back to the single canonical writer on uncertainty.
- Git linked worktrees share refs and object storage. Same-UID permissions
  and advisory ownership are **cooperative race protection, not an OS security
  sandbox**. This exception does **not** authorize llmtool F2/model-initiated
  real-repository mutation, credential access, new provider egress, admin/root
  operations or CI/Runner/ai-loop controller changes.
- Before general two-agent use, record a **real Mac pilot** showing canonical
  branch/HEAD/index and user files unchanged; distinct worktree HEAD/index and
  peer-independent changes during overlapping small tasks; correct dedicated
  venv/import paths; no branch/owner/symlink bypass; preservation after a
  simulated interruption; two SHA-bound Draft PR/CI/review paths. Native F1
  containment acceptance must still execute only from canonical. If a
  required check is absent/failed, keep parallel mode DISABLED and preserve
  all work.

## Before changing Git state

### Required daily executable preflight (B-min)

Before source/Git mutation in a claimed workspace, run the same
[read-only gate](preflight.py) used by the regression tests. This does not
authorize a workspace, replace CLAIM/RELEASE, activate the fixed worktrees,
or turn PID attribution into an automated check. First establish the permitted
task/root and sole writer cooperatively; existing idle PIDs alone are not conflicts.

Use explicit, independently established expected values, not values freshly
derived from whatever checkout happens to be present. The gate checks root,
branch, common-dir, origin, pinned HEAD, owner/mode and symlink/ACL constraints,
logical index and file inventories, and the own-root Python import. Repository
checks never write the checkout or Git state. Optional candidate/report modes
create only new private evidence files outside monitored roots. It never
stages, commits, fetches, installs, cleans or kills. Normal exit **0** prints exactly
`PASS: read-only preflight`; exit **1** prints a fixed sanitized STOP; invalid
arguments exit **2**. Any nonzero result means preserve state and stop mutation.

Run from the authorized workspace with its already trusted Python:

```sh
python3 -B -I /absolute/reviewed/source/skills/safe-git/preflight.py \
  --root /absolute/authorized/root --branch explicit-owned-branch \
  --common-dir /absolute/expected/common-dir \
  --origin git@github.com:davidcatcat/davidcattoolsproject.git \
  --head FULL_APPROVED_HEAD_SHA --uid EXPECTED_OWNER_UID \
  --root-mode APPROVED_OCTAL_MODE --parent-mode APPROVED_PARENT_MODE \
  --baseline /absolute/private/reviewed-baseline.json
```

Invoke the implementation from a reviewed pinned source SHA and verify its file
SHA256 before loading it; do not trust an arbitrary checkout's copy. Replace
each placeholder with the task's pinned value; do not blindly substitute
current Git output as an expectation. The implementation uses `/usr/bin/git`, a
sanitized environment with global/system config excluded, hooks/fsmonitor off,
and no optional locks. Repo-local settings and the installed venv must already
be trusted. This gate deliberately does not run Git filters to compare content.

The baseline is an explicit reviewed allowlisted inventory, not a requirement
that ignored files be absent. It is a regular nonsymlink owner file, mode **0600**,
with exactly four per-path lists: `index`, `tracked`, `untracked`, `ignored`.
Index records are `[path, git-mode, blob-id, stage]` from NUL-delimited entries.
File records are `[path, lstat-mode, SHA256]`, sorted by unique Git paths;
symlink hashes use link target bytes without following. These records allow
local added/removed/changed-path comparison, including logical index changes.
All current ignored files, including authoritative venv data, are covered.
Untracked/ignored additions, removals, mode and content drift reject. Do not use
an empty ignored baseline or silently refresh it after an unexpected change.

For an initially known-safe, claimed checkout, create a candidate with this
actual CLI (the same explicit identity/mode arguments as above):

```sh
python3 -B -I /absolute/reviewed/source/skills/safe-git/preflight.py \
  --root /absolute/authorized/root --branch explicit-owned-branch \
  --common-dir /absolute/expected/common-dir \
  --origin git@github.com:davidcatcat/davidcattoolsproject.git \
  --head FULL_APPROVED_HEAD_SHA --uid EXPECTED_OWNER_UID \
  --root-mode APPROVED_OCTAL_MODE --parent-mode APPROVED_PARENT_MODE \
  --write-candidate /absolute/private-0700-directory/new-candidate.json
```

Candidate mode validates identity/path and stable inventory, creates a new
0600 file, and prints exactly `CANDIDATE: review required` (exit 0). It does
**not** check the candidate against an approved baseline or execute the Python
import gate; it is never PASS, never auto-promotes/replaces an approved baseline,
and grants no mutation authority. The old aggregate-hash format is rejected;
review a new per-path candidate explicitly rather than auto-migrating it.

For daily checks or reconciliation, add
`--report /absolute/private-0700-directory/new-diff.json` to the normal
`--baseline` command above. The report lists only added/removed/changed paths
per category, never raw content. A clean check has empty delta lists; an inventory
mismatch writes the report and still returns STOP/exit 1. Identity/path/invalid
baseline failures may stop before any trustworthy delta can be produced, so a
report is not guaranteed on every rejection. A failed report write also STOPs.

All new outputs require a nonsymlink owner-only 0700 parent, safe ancestors,
and destinations outside canonical, the fixed-workspace parent, the inspected
root and its common-dir. Files are created O_EXCL/O_NOFOLLOW with mode 0600.
Existing files/symlinks are never overwritten; partial evidence stays preserved
on failure. Use a fresh filename for every attempt. Baseline and report paths,
including sensitive filenames, stay local and must not be committed/uploaded.

Compare the private report and candidate against the approved task allowlist
before explicitly selecting that candidate as the next `--baseline`. Never
blindly collect/re-pin after STOP. After an authorized edit,
stage, commit, or expected generated artifact, reconcile the allowed delta and
explicitly re-pin the baseline/HEAD before the next gate. Do not bless unknown
files. Keep sensitive inventory paths/contents local. A task requiring a different
allowlist must record that expectation, not weaken comparison.

The own venv is fixed at `tools/llmtool/.venv`; its directory must not be a symlink.
Its normal interpreter symlink to the trusted Python installation is allowed.
The gate checks `sys.prefix` before importing, then requires `sys.executable`,
`sys.prefix` and resolved `llmtool.__file__` to belong to that root. Python's
already-installed `.pth` startup code still belongs to the trusted runtime
boundary; this is not a sandbox for executing an untrusted venv. `-B` avoids new
bytecode. Inventory is checked again after the import.

Supported boundary: ordinary files/symlinks; macOS ACLs must be absent or consist
solely of `deny delete` entries (the default home ACL, granting no access), and
no group/world-writable ancestors except root-owned sticky system-temp ancestors.
Submodules/special files or unreadable/changing inventories fail closed. This
is cooperative same-UID race protection, not an atomic lock or protection against
a malicious same-UID writer. Check active tasks/children separately; no lease or
PID-only takeover. No G6/G7 authority is conferred.

Targeted validation (no provider/lifecycle suite):

```sh
python3 -B -I -m unittest discover -s skills/safe-git -p test_preflight.py -v
```

These tests initialize their own disposable synthetic Git repositories and
pip-free venvs; they invoke the actual daily CLI for both PASS and STOP, including
real import provenance drift. They do not create project clones/worktrees or
exercise the interrupted fake CLI fixtures tracked in Issue #122.

Run:

```bash
git status --short --branch
git branch --show-current
git remote -v
```

Identify unrelated working-tree changes before staging anything.

## Synchronize

1. Run `git fetch origin`.
2. Compare the current branch with its upstream.
3. If integration is required, prefer a normal fast-forward when possible.
4. If histories diverge or a conflict appears, stop and report the exact state unless the user has explicitly asked you to resolve it.
5. Never discard uncommitted work to make synchronization easier.

## Validation and report provenance

Before merging or cherry-picking a feature branch, validation report, or smoke-test artifact,
check its merge base, ahead/behind counts, graph, and the revision/version actually tested. Confirm
whether the branch was based on an older main and whether the target already contains an equivalent
artifact. Preserve newer target history and avoid duplicate or stale evidence. Prefer safe normal
integration; choose the appropriate method for the branch state without rewriting published history.

## Commit

1. Run the project's required tests/checks first.
2. Review `git diff` and `git diff --staged`.
3. Stage only files belonging to the current task.
4. Use a concise commit message that describes the change.
5. Re-check `git status`.

## Push

Push the current branch only after checks pass and the intended commit is confirmed.

Hard guardrails:

- Never force-push unless the user explicitly requests it and the consequences are explained.
- Never use `git reset --hard` to resolve routine workflow problems.
- Never delete branches or tags unless explicitly requested.
- Never overwrite unrelated remote work.

## Merge evidence discipline

## Delegated PR management and audit attribution

The requirements owner has **delegated routine technical PR/merge decisions**
to the AI coordinator within accepted product goals; do not ask them to
act as PM, review each Low finding or hand-carry Codex/Claude prompts.
Follow [ai-team-delivery](../ai-team-delivery/SKILL.md). Delegation
does not waive independent gates, Mac validation, product/provider
permissions, or the AI-owned controller **pre-publication** review
and exact-revision release gate. Routine controller technical
acceptance is delegated to AI, not the owner. The standard Git
`run.sh` shortcut executes the control branch's tracked
`scripts/ai-loop.sh` and matching cached Python controller; any
control-tip update may affect the next Mac execution. Validate
immutable candidate bytes on a non-executable staging reference:
independent Security review where applicable, exact-candidate
helper CI and required Mac acceptance **before promoting the
same revision to the executable tip**. Record the executed commit
and privacy-safe outcome through actually observed evidence,
not by assuming a custom local logging mechanism exists.

After verifying all contract-required evidence, the coordinator can
merge an eligible exact-head PR without repeatedly asking "may I merge?".
Keep blocker/unfinished PRs Draft or Open, close genuinely obsolete PRs
with a reason, and retain immutable evidence. Do not merge a failing
controller helper just because Linux CI is green. Never treat the
Mac operator's ability to type a command as permission for that command
to run arbitrary new Git-fetched code.

In the audit trail record **technical disposition author, reviewer,
product scope confirmation, delegated merge authority, Git executor,**
and exact refs separately. Do not attribute the coordinator's detailed
risk analysis to the owner. Do not equate "delegated technical choice"
with new powers to export credentials/data, spend unapproved funds,
bypass branch protection or lower a required security gate.

Before merge, verify the latest PR/head SHA and use exact-head checks. A report-only
or documentation commit after a previously green implementation creates a new head;
verify that head's required CI rather than citing only the older run. After merge,
verify the new main SHA and any required post-merge CI. Green CI alone does not replace
a required independent review, bug hunt, security review, QA or live/runtime gate.

Keep one Git writer per publication batch when multiple agents are involved.

## Cloud/Mac evidence gate

For llmtool work, follow [the development workflow](../../tools/llmtool/WORKFLOW.md).
Publish only the authorized feature branch; branch publication does not establish
merge readiness. Verify required Cloud/CI/review/Mac results against the affected
implementation revision before merge. Keep pending required Mac checks blocking,
and never use a new checkout to bypass Git permissions.

## Completion criteria

- Local and remote state are understood.
- Required checks pass.
- Commit contains only intended changes.
- Push succeeds, or the blocking reason is reported precisely.
