# Safe Git reference: owner-approved two-fixed-worktree exception

Normative reference for [safe-git](SKILL.md). Moved verbatim from `SKILL.md`
(main `cd7ee64`) so the skill stays short; the policy text below is unchanged.
Status: G7 activation-status PR #126 is merged into `main` (`83be9f6`).

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
