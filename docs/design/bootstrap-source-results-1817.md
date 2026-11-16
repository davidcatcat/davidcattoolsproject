# Recording results that change D6 bootstrap sources (#1817)

Status: design only. Source: `main` at `d6c1d018`. Defect: #1817 comments c6928715060 and c6928733279.
References are `file:line` at that commit. "UNKNOWN" marks facts outside this Task's read paths.

## 1. Problem

A Task whose write paths include a D6 bootstrap source (for example `tools/llmtool/pyproject.toml`)
can commit the change, but `record_result` always fails with
`ENV_BOOTSTRAP_REJECTED:D6_BOOTSTRAP_SOURCE_MISMATCH`. The result head is never recorded, so the
Task cannot get an EC2 remote-test grant, a PR or a merge.

## 2. Current behaviour

- **Publish.** `task_advance._publish_task` (`tools/llmtool/llmtool/task_advance.py:1890`) reads the Task
  write paths (`:1905`), refuses changes outside them (`:1937`, `:1961`), commits, merges `origin/main`
  (`:1970-1997`) and calls `record_result` (`:1999-2007`, loader at `:1880-1887`). Only `task_id`,
  `canonical_root` and `root` are passed. The write paths do not reach the lifecycle.
- **record_result** (`tools/llmtool/scripts/d6_worktree_lifecycle.py:904`) refuses
  `env2_approved_tooling_sha` records (`:957-958`) and dirty trees (`:988-990`). It writes the new
  `head_sha` (`:1000-1002`), then runs ENV-2 through `_bootstrap` (`:1007`). On failure it restores the
  previous record (`:1008-1018`). On success it requires `env2_pass_marker.current_head_sha == head`
  (`:1021-1025`).
- **ENV-2 launch.** `_bootstrap` (`d6_worktree_lifecycle.py:647`) runs the helper from the pinned dispatcher
  checkout (`:536-548`, `:685-690`). The helper copy in the Task tree is never executed. Any
  `STOP (<code>)` becomes `ENV_BOOTSTRAP_REJECTED:<code>` (`:696-701`).
- **The check.** `validate_registered_worktree` (`tools/llmtool/scripts/d6_worktree_env.py:537`) sets
  `tooling_sha = env2_approved_tooling_sha or base_sha` (`:599-605`). It calls
  `_validate_bootstrap_sources` (`:468-511`). For each of the five required sources (`:470-476`) and
  three optional build inputs (`:477-481`), the working-tree bytes and exec bits must equal the
  tooling-commit blob (`:495-499`). Optional inputs must also stay absent when they are absent there
  (`:485-494`). The helper itself must match too (`:503-511`). Any difference raises
  `D6_BOOTSTRAP_SOURCE_MISMATCH`. The check uses only `tooling_sha`; it does not look at the head commit.
- **The build.** `_invoke_bootstrap` (`d6_worktree_env.py:961`) clears the marker (`:965`), then runs
  `make env-bootstrap` from the **Task tree's** `tools/llmtool/Makefile` with `WORKTREE_ENV_ROOT=../..`
  (`:987-993`). The byte-equality check is therefore what keeps the build on trusted sources.
- **ENV-2 markers.** The helper writes the PASS marker under the lifecycle lock
  (`_update_env2_pass_marker`, `d6_worktree_env.py:817-909`). The report must be bound to `head_sha`
  and `tooling_sha` (`:891-898`). The lifecycle checks the marker at `d6_worktree_lifecycle.py:1021-1025`.
- **Where write paths live.** They are stored only in the Task store record
  (`developer.write_paths`), checked in `tools/llmtool/llmtool/task_store.py:2900-2910`. Callback
  evidence must stay inside them (`task_store.py:3155-3157`). The D6 registry record has no write paths
  (`d6_worktree_lifecycle.py:783-809`). `remote_test._task_path_modules` uses them only to select
  tests (`tools/llmtool/llmtool/remote_test.py:313-323`).
- **Downstream gates.** The remote-test grant is bound to the D6 recorded head
  (`remote_test.py:562-595`). The merge gate requires `recorded_result_head == result_head` and a clean
  D6 (`task_advance.py:3246-3265`; snapshot at `:2539-2572`).

The code matches the goal's premise, so this is not a SPEC_MISMATCH. The fix can stay in the
ENV-2/lifecycle seam. EC2 and the merge gate already follow the recorded head.

## 3. Design

### 3.1 Binding write paths to the ENV-2 check

1. `_record_d6_result` passes `write_paths=record['developer']['write_paths']`. `_publish_task` has
   already validated these (`task_advance.py:1905-1912`).
2. `record_result(..., write_paths=None)` intersects them with the fixed bootstrap set
   `BOOTSTRAP_SOURCES`: the 5 required sources, the 3 optional inputs and
   `tools/llmtool/scripts/d6_worktree_env.py`. Under the lock it writes the result into the same record
   update as `head_sha` (`:1000-1002`):
   `env2_source_allowance = {schema_version: 1, head_sha, base_sha, paths: sorted(...),
   write_paths_sha256}`. If the intersection is empty, or `write_paths` is `None` (the CLI
   `--record-result-task-id` path at `:3073`), it removes the key. The failure rollback at `:1015`
   restores the old record, including the old allowance.
3. Only `record_result` writes this key. `create_worktree` never sets it, and `resume_worktree` only
   reads it.

### 3.2 ENV-2 acceptance rule (fail closed)

`_validate_bootstrap_sources` returns a mode. For each path P in `BOOTSTRAP_SOURCES`:

- If the local bytes and mode equal the tooling blob, P is unchanged.
- Otherwise P is an **allowed change** only if **all** of these hold:
  1. `env2_approved_tooling_sha` is unset, so `tooling_sha == base_sha`.
  2. A valid allowance exists, with `head_sha` equal to the record `head_sha` and to the live HEAD,
     and `base_sha` equal to the record `base_sha`.
  3. P is in `allowance.paths`.
  4. The local bytes and exec bits equal the blob of P at **head**, read with `_base_blob(root, head, P)`.
     The file is not group- or world-writable. A required source deleted at head is refused.
- Any other difference keeps `D6_BOOTSTRAP_SOURCE_MISMATCH`. This includes paths outside the write
  paths, a stale or missing allowance, approved-tooling records and bad modes. A malformed allowance
  raises `D6_REGISTRY_INVALID`.
- If there are no allowed changes, the mode is `BUILD` and nothing changes from today. If there is at
  least one, the mode is `CARRY`.

### 3.3 The environment stays on trusted sources (CARRY mode)

In `CARRY` mode, `_invoke_bootstrap` **does not run make**. It reads no changed bootstrap source as
build input. The D6 keeps the venv from the last full PASS, which was built from `tooling_sha`
sources: by `create_worktree` (`d6_worktree_lifecycle.py:835`) or by a later `BUILD` record. Before
clearing the marker (`d6_worktree_env.py:965`), the helper requires that marker to have
`result == "PASS"`, the same `task_id` and `root_realpath`, the current `tooling_sha`, a valid
`environment_fingerprint`, and `report_sha256` equal to the SHA-256 of the current report file.

It then writes a report bound to the new head with `status: "env_carried"`, the carried
`cache_fingerprint`, `operations: "NONE"` and `allowed_bootstrap_paths`. It publishes a marker that
adds `environment_mode: "CARRIED"` and `carried_from_head_sha`. If there is no prior marker, or it does
not match, the helper refuses with a new code `ENV2_CARRY_MARKER_UNAVAILABLE`. It never falls back to
building. The PM handles recovery.

So the D6 venv never reflects the Task's changed `pyproject.toml`. That is intended: the result is
tested on EC2. Executing the changed sources would require a Makefile, `worktree_env.py` and pip
redesign. That code is outside this Task's read paths, so how much of it could take trusted inputs is
UNKNOWN. CARRY avoids that redesign.

### 3.4 EC2 remote test and merge

- **EC2.** After `record_result` succeeds, the existing grant (`remote_test.py:562-597`) binds to the
  recorded head with no change. How the EC2 runner builds its own environment from the head is in
  `tools/ec2-runner/` (UNKNOWN here). Its existing trust boundary for PR heads is unchanged.
- **Merge gate.** No code change is needed (`task_advance.py:3246-3265`). A CARRIED marker makes no
  claim about the head's environment. The evidence is the EC2 run, CI and the formal review. After
  merge, the changed source becomes a trusted tooling source only for D6s created from the new `main`.
- **Not fixed here.** `_publish_task` merges `origin/main` before recording (`:1970-1997`). If `main`
  changed a bootstrap source that is not in the Task's write paths, the record still refuses, as today.
  That is a separate defect.

## 4. Ordered PRs

Rule files are the entries in `tools/llmtool/sensitive-paths.json` and need Codex security review.
The exact test module names are UNKNOWN because `tools/llmtool/tests/` is outside this Task's read
paths. Tests follow the Owner #763 A rule: synthetic `pyproject.toml` content such as a version bump,
and no credential-shaped strings.

1. **PR-1 lifecycle binding (rule files: `d6_worktree_lifecycle.py`, `task_advance.py`).**
   - Code: `write_paths` goes through `_record_d6_result` into `record_result`, which writes or removes
     `env2_source_allowance`.
   - Tests: the allowance is exactly the intersection with `BOOTSTRAP_SOURCES`, bound to the head; an
     empty intersection or the CLI path removes it; rollback restores the previous allowance; publish
     passes the Task write paths.
   - Any `task_completion.read_d6_registration` key check must accept the new key; that file's
     behaviour is UNKNOWN. ENV-2 on current `main` ignores unknown record keys (`d6_worktree_env.py:244-283`,
     `:558-577`), so PR-1 alone does not change behaviour.
2. **PR-2 ENV-2 acceptance and CARRY (`tools/llmtool/scripts/d6_worktree_env.py`; not listed in
   sensitive-paths.json; security review recommended).**
   - Code: §3.2 and §3.3.
   - Tests: an allowed `pyproject.toml` change gives CARRY with the carried marker and make is not
     called; `D6_BOOTSTRAP_SOURCE_MISMATCH` is kept for a path outside the allowance, a stale allowance
     head, approved tooling, bytes differing from the head blob, a group-writable mode, a deleted
     required source and an optional input added without allowance; a missing or mismatched prior
     marker gives `ENV2_CARRY_MARKER_UNAVAILABLE`; BUILD mode is unchanged.
   - **Bootstrap blocker:** this PR changes the helper, which is itself a bootstrap source
     (`:503-511`). Under current `main`, its own `record_result` fails with this same defect. The
     delivery route needs an Owner decision through the PM (UNKNOWN).
3. **PR-3, optional (rule file: `tools/llmtool/sensitive-paths.json`).** Add the
   `BOOTSTRAP_SOURCES` paths so that changes to them also need security review. This needs an
   Owner decision. Test: the sensitive-path classification test for the new entries.

Once PR-1 and PR-2 are on `main` and the dispatcher is pinned to them, a Task such as the #1817
`pyproject.toml` change can record its result, get an EC2 grant, open a PR and merge through the
existing gates.
