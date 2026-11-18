# Bootstrap-source refusal investigation (#1817)

Status: read-only investigation note. It names every branch in
`tools/llmtool/scripts/d6_worktree_env.py` that can refuse with
`D6_BOOTSTRAP_SOURCE_MISMATCH`, says which branch the observed refusal matches,
and lists what PR-3 must change. No code or test is changed.

Source: the task working tree at `source_sha`
`0eb1838853ccece3628931d272bb9b10935127b4`. Line numbers are from that tree.
Only `tools/llmtool/scripts/d6_worktree_env.py` and
`docs/design/bootstrap-source-results-1817.md` were read. Any statement about a
file outside that read scope is marked UNVERIFIED, even when the design note
cites it.

## 1. Where the code is raised

`main` (`d6_worktree_env.py:1055-1069`) calls `validate_registered_worktree`
(`:1068`) and then `_invoke_bootstrap` (`:1069`), which revalidates at `:964`.
Both paths reach the same check, so the refusal fires before any build.

`validate_registered_worktree` accepts an approved tooling SHA only when it equals
the live head (`:599-604`), then sets

    tooling_sha = approved_tooling_sha or record["base_sha"]

at `:605` and calls `_validate_bootstrap_sources(root, base_sha, head, tooling_sha)`
at `:606`. The check therefore compares the working tree with the **tooling**
commit, not with the head. The design note states the same (`:34-40`).

`_validate_bootstrap_sources` (`:468-511`) defines:

- five required sources (`:470-476`): `tools/llmtool/Makefile`,
  `tools/llmtool/.gitignore`, `tools/llmtool/scripts/worktree_env.py`,
  `tools/llmtool/worktree-env.lock`, `tools/llmtool/pyproject.toml`;
- three optional build inputs (`:477-481`): `tools/llmtool/setup.py`,
  `tools/llmtool/setup.cfg`, `tools/llmtool/MANIFEST.in`;
- the helper itself (`:503`): `tools/llmtool/scripts/d6_worktree_env.py`.

## 2. Every branch that raises `D6_BOOTSTRAP_SOURCE_MISMATCH`

| # | file:line | Triggering condition | Paths affected |
|---|---|---|---|
| 1 | `d6_worktree_env.py:483-487` | `_base_blob(root, tooling_sha, relative)` finds no entry in the tooling commit **and** the path is one of the five required sources (`:486-487`). | Required sources only. |
| 2 | `d6_worktree_env.py:488-494` | An optional input is absent from the tooling commit but exists in the working tree (`lstat` succeeds), so it was added locally (`:494`). | `setup.py`, `setup.cfg`, `MANIFEST.in`. |
| 3 | `d6_worktree_env.py:495-499` | For a path present in the tooling commit, local bytes differ from the tooling blob (`local != committed_bytes`), the executable bit differs, or the local mode is group- or world-writable (`local_mode & 0o022`). | Required sources and optional inputs that exist at `tooling_sha`. |
| 4 | `d6_worktree_env.py:504-506` | The helper `tools/llmtool/scripts/d6_worktree_env.py` has no entry in the tooling commit (`committed_helper is None`, `:506`). | Helper only. |
| 5 | `d6_worktree_env.py:507-511` | The helper's local bytes differ from the tooling blob, its executable bit differs, or its mode is group- or world-writable (`:509-511`). | Helper only. |

These five are the only `_reject("D6_BOOTSTRAP_SOURCE_MISMATCH")` calls in the
file (`:487`, `:494`, `:499`, `:506`, `:511`). Nearby failures use different
codes and are not this refusal: `D6_BOOTSTRAP_SOURCE_INVALID` (`:400`, `:435`,
`:441`), `D6_BOOTSTRAP_SOURCE_UNAVAILABLE` (`:405`, `:462`, `:464`),
`D6_BOOTSTRAP_SOURCE_CHANGED` (`:413`, `:422`), and the local-inventory codes
(`:387`, `:340`).

The lifecycle turns whatever code the helper writes as `STOP (<code>)` into
`ENV_BOOTSTRAP_REJECTED:<code>` (`docs/design/bootstrap-source-results-1817.md:31-33`,
citing `d6_worktree_lifecycle.py:696-701`). That mapping file is outside the read
scope; the mapping itself is UNVERIFIED here. The helper's own output is the
generic `STOP: ... (<code>)` at `:1071-1077`; it does not name a branch.

## 3. Which branch matches the observed refusal

**Documented #1817 reproduction: branch 3, `d6_worktree_env.py:497-499`.**
The design note's problem statement (`:15-18`) is a Task whose write paths
include a bootstrap source, for example `tools/llmtool/pyproject.toml`, that
commits the change and then fails ENV-2. In that case `env2_approved_tooling_sha`
is unset, so `tooling_sha == base_sha` (`:605`); the committed change makes the
local `pyproject.toml` bytes differ from the `base_sha` blob, which is branch 3.
PR-3 changes the helper itself, which is also a bootstrap source, so PR-3's own
result record would instead hit branch 5 (`:509-511`).

**The two PM Task records 2af76699 and 3cdbf07e: UNKNOWN.** Their Task-store and
D6 registry records, their `developer.write_paths`, and the resulting diff are
outside this Task's read scope, so the note cannot tell whether the refused path
was `pyproject.toml` (branch 3), the helper (branch 5), an added optional input
(branch 2), or a missing tooling entry (branch 1 or 4). The refusal text is
generic, so the branch cannot be recovered from the code path alone. This is the
reason A2 can only say UNKNOWN for those records.

Branches 1 and 4 require the tooling commit itself to lack a source, which is
not the situation described in the design note. Branch 2 requires adding an
optional build input. Neither is supported for the observed records by any file
in the read scope.

## 4. What PR-3 must change

PR-3 is "ENV-2 acceptance, CARRY and venv identity" and changes the helper only
(`docs/design/bootstrap-source-results-1817.md:351-370`). The design note's status
line (`:3`) says PR-1 (#2242) and PR-2 (#2254) are merged and PR-3 is not
started; that PR state is taken from the note and is UNVERIFIED against code
outside the read scope.

1. **ENV-2 acceptance rule (design §3.2), in
   `tools/llmtool/scripts/d6_worktree_env.py`.**
   - `_validate_bootstrap_sources` (`:468-511`) must return a mode instead of
     rejecting every difference. An allowed change requires: approved tooling
     unset; a valid `env2_source_allowance` whose `head_sha` matches the record
     head and live HEAD and whose `base_sha` matches the record; the path in
     `allowance.paths`; and local bytes and exec bits equal to the blob at
     **head**, read with `_base_blob(root, head, P)`.
   - `validate_registered_worktree` (`:537-617`) must pass the record allowance
     and head into the check. The record is already read at `:556`; the head is
     already computed at `:590`. The allowance is written by PR-1 in the
     lifecycle code (UNVERIFIED).
   - Every case outside the allowance keeps `D6_BOOTSTRAP_SOURCE_MISMATCH`,
     including branches 1-5 above, approved-tooling records and bad modes.
     A malformed allowance must raise `D6_REGISTRY_INVALID`.

2. **CARRY mode (design §3.3), in `_invoke_bootstrap` (`:961-1028`).**
   - Before `_update_env2_pass_marker(root, task_id, None)` at `:965`, run the
     three fail-closed checks: historical marker, historical report SHA-256, and
     live `tools/llmtool/.venv` identity. The prior `current_head_sha` must be an
     ancestor of, or equal to, the new head.
   - In CARRY mode do not run `make` (`:986-993`) and do not execute anything
     from the Task tree or venv. Write the carried report and publish a carried
     marker.
   - Add the refusal codes `ENV2_CARRY_MARKER_UNAVAILABLE` and
     `ENV2_CARRY_ENVIRONMENT_UNVERIFIED`; never fall back to a build.

3. **Carried marker (C1, design §3.5), in `_update_env2_pass_marker`
   (`:817-909`).**
   - Keep the existing nine-key `schema_version: 1` marker for BUILD
     (`:899-909`).
   - For CARRY, publish the twelve-key `schema_version: 2` shape: the nine keys
     plus `environment_mode: "CARRIED"`, `carried_from_head_sha`, and
     `allowed_bootstrap_paths`. Keep the report binding checks at `:891-898`.

4. **Prior marker and report reading (C2, design §3.3 checks 1-2).**
   - New code in this file must apply the marker shape/binding rules and read
     the prior report with the same no-follow, owner-only, `0o600`, single-link
     rules as `:856-885`. A prior `current_head_sha` is an ancestor of, or equal
     to, the new head instead of equal to it.

5. **Venv identity (design §3.4), in `_invoke_bootstrap` and the report path.**
   - After every BUILD PASS, compute `venv_identity` from the live venv before
     `_atomic_bootstrap_report` (`:1025`) and add it to the report. Build
     behaviour stays unchanged when the layout is unexpected
     (`venv_identity: "UNKNOWN"`).
   - In CARRY, verify the live `tools/llmtool/.venv` against the recorded
     `venv_identity` (directory, `pyvenv.cfg`, interpreter target and stat, one
     `site-packages`, `RECORD` hashes). Failure is
     `ENV2_CARRY_ENVIRONMENT_UNVERIFIED`.
   - First confirm the venv layout in
     `tools/llmtool/scripts/worktree_env.py`. That file is outside the read
     scope, so the assumed layout is UNVERIFIED.

6. **Tests (design §4 item 3).** Add or update tests under
   `tools/llmtool/tests/`; the exact module names are outside the read scope and
   are UNVERIFIED. Required coverage from the note (`:354-366`): allowed
   `pyproject.toml` change gives CARRY without `make`; BUILD reports gain
   `venv_identity`; each `D6_BOOTSTRAP_SOURCE_MISMATCH` case is kept; marker and
   report failures give `ENV2_CARRY_MARKER_UNAVAILABLE`; venv failures give
   `ENV2_CARRY_ENVIRONMENT_UNVERIFIED`; a second CARRY keeps the original
   `carried_from_head_sha`; and the end-to-end `record_result` path. Tests follow
   the Owner #763 A rule: synthetic fixture content only, no credential-shaped
   strings, no production check weakened.

7. **Delivery blocker (design §4 item 3, `:367-370`).** The helper is itself a
   bootstrap source (`:503-511`), so under current `main` PR-3's own
   `record_result` hits branch 5. The delivery route needs an Owner decision
   through the PM; that decision is UNKNOWN. D6s created before PR-3 carry no
   `venv_identity`, so they cannot CARRY until a fresh BUILD record; that is the
   fail-closed outcome.

8. **Out of scope for PR-3.** The design note says PR-1 and PR-2 already cover
   the lifecycle binding and completion-marker consumers (`:301-350`), and that
   EC2 and the merge gate need no change (`:277-288`). Those code paths are
   outside the read scope and are UNVERIFIED. The separate defect where
   `_publish_task` merges `origin/main` before recording is not fixed by PR-3
   (`:286-288`).

## 5. Unverified items (outside the read scope)

- `d6_worktree_lifecycle.py` refusal mapping and allowance write (design note
  cites `:696-701`, `:1000-1018`); PR-1/PR-2 merge state.
- `task_completion.py` marker consumer (C4) and `task_submission_cli.py`
  caller (C4c).
- `tools/llmtool/scripts/worktree_env.py` venv layout.
- Test module names and contents under `tools/llmtool/tests/`.
- `tools/llmtool/sensitive-paths.json` classification.
- PM Task records 2af76699, 3cdbf07e and the D6 registry records.
- The Owner decision on the PR-3 delivery route.
- Whether the live `main` equals the task `source_sha`; no Git or network
  command was run.

## 6. Checks

No test or docs check was run: this route has no shell. The acceptance item
"check (docs) passes" is therefore UNKNOWN, not claimed.
