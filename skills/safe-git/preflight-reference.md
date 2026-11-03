# Safe Git read-only preflight

This reference preserves the required Mac workspace preflight. Before any source or Git mutation in a claimed workspace, follow this gate; a nonzero result means preserve state and stop. It is separate from the ordinary root/status checks and does not establish task ownership or permission by itself.

### Required daily executable preflight (B-min)

Before source/Git mutation in a claimed workspace, run the same
[read-only gate](preflight.py) used by the regression tests. This does not authorize a workspace, replace task registration, or turn PID attribution into an automated ownership check. First establish the permitted
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
and destinations outside trust.json `canonical_root` and `workspace_root`, the inspected
root and its common-dir. Every run first reads the owner's
`~/.config/llmtool/trust.json` with the embedded llmtool trust reader and root
validation (physical non-symlink owner directories, no group/world write, no
overlap); a missing or invalid trust file or root stops before any Git work with
`STOP: preflight rejected (TRUST_<CODE>)` (for example `TRUST_MISSING`). Files are created O_EXCL/O_NOFOLLOW with mode 0600.
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
