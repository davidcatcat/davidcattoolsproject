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

**Operational mode: ACTIVE only when the independently reviewed activation-status
documentation PR containing this record has merged into `main`.** While this
record exists only on a PR branch, the published `main` policy remains
**DISABLED**. Once merged, ordinary development is permitted to exactly one
trusted Codex CLI in its fixed Codex worktree and one trusted Claude CLI in its
fixed Claude worktree, each with a single owner/writer and an independently
verified clean task boundary. The canonical integrator/native-acceptance root
remains separate; activation is NOT permission for any additional project root.

**G7 activation evidence and immutable source bindings:**
- Requirements-owner [narrow two-root approval in #110](https://github.com/davidcatcat/davidcattoolsproject/issues/110#issuecomment-6106421018); accepted policy base
  `2ba7925d1bb7bc22732642f2358b1ba0aab5718c` (prior `main`),
  reviewed G1 v2.1 [plan](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6107047473)
  SHA256 `38708c1ec0ba77510cc66b008290a3bdf4404a62b7434ce0705ce371d3a0806d`,
  and G1 [independent review](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6107190863).
- G5 [independent native review](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6111243764)
  and [Coordinator acceptance](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6111265049):
  two overlapping real developer windows; separate own-root venvs, frozen
  SHA-bound PRs #119 (`0cec8f47`) and #121 (`d9706353`) with CI/review.
- G6 approved [native Annex DRAFT 2.1](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6112962040)
  ROW-block SHA256 `12bf30daf33ee2a203de44e305172c4dc86d38f7ff94ba584dc7ed345df65118`;
  r01-r07/r09 [independently accepted](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113673728);
  r08 invocation SHA256 `bc3048632aadecb2b5534eb3c97d9e2547c21fd353ec99a7cd16d47fcaf8c1d3`
  [independently accepted](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6114027126);
  r10 is evidence-only, satisfied by merged PR #125 (`2ba7925d1bb7bc22732642f2358b1ba0aab5718c`),
  [exact-head CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38199694423)
  and [independent Mac evidence approval](https://github.com/davidcatcat/davidcattoolsproject/issues/122#issuecomment-6118492559).
  See [G6 Coordinator consolidation](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6118538797).
- G7 independent [activation-evidence verdict: PASS_ACTIVATE_TWO_ROOTS, conditional
  C1-C6](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6118588175).
  This activation-status PR itself still requires a **different, independent
  exact-HEAD Security/Functional documentation review and applicable CI before
  merge**. A review comment or OPEN/Draft PR does not activate anything.

This historical section heading is deliberately retained to preserve existing
Markdown anchors; the **Operational mode** line above, only on merged `main`,
determines the actual state.

Canonical integrator and required native acceptance root (must never be moved
or cleaned as part of allocation):
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

The **only** eligible fixed developer roots, during an explicitly authorized
#115 limited pilot or after formal activation:
- Codex CLI: `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`
- Claude CLI: `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude`

### 2026-10-19 Owner D1: OpenCode takes over the existing second fixed root — NOT YET LIVE

[Owner D1–D5 decision](../../docs/decisions/llmtool_opencode_secondary_developer_20261019.md) approves **reassigning ownership, not adding a worktree**. The existing physical root ending `/claude` will remain at that pathname but become the exclusive OpenCode Developer root **only after** independent Security/Functional review of the ownership rule and source-bound **Mac native release/C2/C3 evidence**. Its earlier two-root G7 acceptance proves the historical Codex+Claude layout; it does not automatically prove transfer to OpenCode. Do not treat a GitHub decision or document merge as physical activation.

First independently verify the paused Claude Developer's real Session/descendants/tests are released; inspect exact root/common-dir, branch/HEAD/index, tracked/untracked/ignored files, Git locks, permissions and dedicated venv/import identity. Preserve old work, retain unknown data and STOP if another writer or incomplete release is found. Only then admit **one** task-bound OpenCode writer in that root, with distinct OpenCode CLI/Session/task/branch evidence. Claude **Reviewer** must use separately authorized *read-only* review context and may not interfere with OpenCode Git refs, worktree or tests. The paused Claude **Developer** must not keep writing in the second root.

Owner D4 permits OpenCode to **read the entire non-sensitive repository**, not secrets, arbitrary external Mac directories, automatic provider data egress, writes outside the authorized task or llmtool F2. Verify the installed OpenCode **v2** permission schema, effective read/search/shell/task/external-directory effects, provider-specific privacy/egress authorization, redaction and negative leakage tests **before real external source transfer**. Prompt-only rules are not an OS boundary. Preserve all the C2/C3, single-writer, shared-ref and existing Git-authority gates below; **no third checkout, rename, clone, cleanup, forced branch operation or automatic paid fallback**.

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

### Activated task turnover, session binding and safe fallback

- **C2 session binding:** Each developer must open its own CLI with actual session
  CWD set to its assigned fixed root. Codex works in its **Codex** root under
  the owner-selected permission mode below (`workspace-write` by default), and
  Claude uses its separately assigned **Claude** root.
- **Codex permission mode (owner authority):** The owner has authorized the
  original Codex Developer to use `danger-full-access` / `approval_policy=never`
  ([decision](../../reports/llmtool_codex_danger_full_access_audit_decision_20261013.md),
  [owner direction](https://github.com/davidcatcat/davidcattoolsproject/issues/135#issuecomment-6152425440)).
  The permission mode is the **owner's decision**. AI reviewers and
  coordinators find, fix and disclose risks, but have **no authority to veto,
  revoke, defer or ask the owner to re-authorize it**. A failing or incomplete
  audit review is not a reason to stop such a session at C2 or to relabel the
  owner's authorization as denied. Report these three states separately, and
  never infer one from another:
  - `OWNER_AUTHORIZED_FULL_ACCESS`: the owner's choice, as recorded above.
  - `EFFECTIVE_MAC_MODE_NOT_VERIFIED` until the named session's actual
    Codex sandbox/approval settings are inspected on the Mac. GitHub text
    cannot change or prove a local session mode.
  - `NATIVE_AUDIT_NOT_VERIFIED` until native hook coverage, deny and
    fail-open behaviour are proven. The merged collector is observe-only and
    cooperative: an unrestricted process can bypass or disable hooks, so it is
    not an OS sandbox. Never claim complete or verified audit without that
    evidence.

  Full access removes the OS write/network sandbox and approval prompts, so
  mistakes have a larger blast radius. It never waives task
  allowlists, worktree ownership, this C2/C3 preflight, restricted Git/OS
  operations, credential/secret handling, or separate real-provider/egress
  and F2 permissions. If a safety requirement would block an owner-authorized
  operation, report the exact risk and an owner-controlled option instead.
- **Session identity:** Check the effective session/root identity,
  `git rev-parse --show-toplevel`, branch, common-dir and owned writable paths;
  an app-server/helper PID ancestry alone is not an identity proof. Canonical
  integrations, shared refs and native F1 acceptance remain serialized under
  the integrator. A reviewer must not fetch in canonical as an unclaimed side effect.
- **C3 branch turnover:** Existing pilot branches are already merged and must
  not be silently reused for new work. Before each new task in an assigned root,
  confirm the old task's tests/children have ended and all code/evidence is
  published; verify the root is clean with **tracked, staged, unstaged,
  untracked and ignored data preserved** and no conflicting writer, Git lock or
  partial work. Establish task ownership and run the pinned read-only
  `--baseline` preflight against its *already approved* old-branch baseline.
  Perform only a verified, bounded no-prune/no-tags `git fetch origin`
  using the approved default refspec, which updates the **shared**
  `refs/remotes/origin/main` in the common Git directory; verify remote HEAD
  and stop on a ref-lock or fetch failure. Then create a fresh,
  unique, agent-owned branch without force, for example
  `git switch --no-track -c <agent-unique-task-branch> origin/main`.
  **Never** use `git switch -B`, `-f`, `reset --hard`, `stash` or check out
  `main`/the peer branch in a developer root. After switching, verify new
  HEAD/source/working tree and use a separately reviewed, exact-branch new
  inventory/baseline under the existing preflight process **before writing**;
  never silently re-pin the old G6 or other approved baseline. The integrator
  alone handles old branch-ref retirement under a separate reviewed scope;
  developer agents must not prune/delete any ref or worktree.
- **Task-final evidence at publication:** Before retiring each completed source task, retain a distinct off-repo task-final inventory/candidate pinned to the actual published branch HEAD, separate from the independently accepted task-start baseline. The next C3 handoff must independently reconcile the final source delta and run the formal pinned baseline gate. Never represent the original pre-edit baseline as an unchanged post-commit inventory or silently regenerate an accepted baseline. A missing final artifact permits one bounded evidence-only recovery, not repetition of product tests or a claim of source corruption ([Issue #135](https://github.com/davidcatcat/davidcattoolsproject/issues/135#issuecomment-6155426463)).
- **Batch reports, not mandatory gate removal:** Report routine same-task state/claim/verification/release evidence as one cohesive Developer work package where actual ordering permits, with separate genuine Integrator ACK and independent candidate acceptance where required. Do not ask the owner to ferry every micro-gate prompt. An in-session child Reviewer can pre-review but cannot grant a shared-ref lease, independently accept a C3 baseline, mutate a peer root or substitute for non-author security approval. GitHub comments remain records, not Mac session commands ([Owner decision](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6155769235)).

- **Per-root runtime:** After branch turnover, verify
  `sys.executable`, `sys.prefix`, `llmtool.__file__` and
  `pyvenv.cfg` `home` still resolve to the agent's own approved root/runtime;
  an existing editable install may stay only if provenance remains correct.
  Any missing dependency, interpreter drift, wrong-root import or need to
  change/install packages is STOP pending a separately approved venv update.
- **C4 operating limits:** G6 tested cooperative same-UID safety, **not OS
  isolation**; claims are advisory, not atomic locks. Real crash containment
  and actual malicious/accidental peer-venv poisoning were NOT_VERIFIED
  (synthetic fail-closed cases were). Always check live writer/child/process
  ownership and exact root/import provenance before mutation. On crash or
  unknown partial state, **STOP and preserve**, fall back to the single
  canonical CLAIM/RELEASE integrator route; never auto-clean/retry. On external
  SSH/GitHub/auth errors, absolute git-dir/remote mismatch or shared-ref
  conflict, STOP rather than assuming empty results. A failed fetch can grow
  the object store; no transactional rollback is promised. Historical Mac
  filesystem and interpreter skips remain documented, not silently converted
  to PASS for unrelated future tests.
- **C5 scope unchanged:** llmtool F2/model-initiated real-repository mutation is
  NOT AUTHORIZED; native F1 containment remains canonical-only. No third
  checkout/clone/archive copy, no worktree unlock/move/remove/prune, no
  automated kill/cleanup of the historical 70 `test-model` PIDs, and no
  new CI/Runner/ai-loop controller authority. G6 accepted evidence remains
  private/preserved; links and hashes in GitHub are the durable record,
  because `/private/tmp` can be cleared on restart or periodic maintenance.

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

## D-007 Codex Cloud / GitHub branch separation (owner decision 2026-10-17)

[Architecture Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172)
accepts **independent mobile-initiated Codex Cloud development**, not
llmtool F2 Git-write capability. Coordinator/Integrator may split out
a Cloud-suitable nonoverlapping work package; the owner starts it from
the mobile app. Cloud uses only its **task-configured isolated checkout**,
not the canonical Mac repo or a locally agent-created extra worktree.

- **Before Cloud source changes:** pin unique task owner, permitted paths,
  base/head, expected outcome and tests; compare local/other Cloud task
  ownership. Cloud VM independence does not isolate **shared GitHub refs**.
  Conflicting branch/file ownership means STOP and Integrator reconciliation.
- **Publication:** only the Cloud-owned feature branch and eligible
  **Draft PR**, with verified GitHub permission, observed commit/head,
  diff/CI and actual test evidence. Do not assume push permission, overwrite
  peer branches, force push or merge main automatically.
- **Review handoff:** Cloud developer/self-agent review is **pre-review**
  only. Under the **2026-10-18 handoff disposition, the existing Integrator
  picks up the completed PR through actually available, authorized CLI/GitHub
  workflows**. Owner link sharing remains an optional fallback. If remote
  launch is unavailable, record the concrete task and limitation; a GitHub
  comment does not prove that Cloud has started. The Integrator checks
  exact task/diff/revision,
  obtains required **distinct non-author** Security/Functional/QA and
  Mac-native acceptance, and decides next integration steps only with
  valid evidence. The earlier 2026-10-17 mobile handoff remains available
  as an optional fallback under this disposition, not a required owner relay.
- **No false PASS:** Cloud CI/offline tests do not establish Mac VPN/auth,
  native process/OS behavior, production company LLM, F2 actual local
  mutation, original Integrator Session wake, or full A11c M2 cutover.
  Keep unavailable required gates NOT_TESTED/BLOCKED.

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
