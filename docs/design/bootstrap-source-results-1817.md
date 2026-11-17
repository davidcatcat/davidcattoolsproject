# Recording results that change D6 bootstrap sources (#1817)

Status: design only, revision 3. Revision 2 fixed codex-reviewer v1 findings F1 (marker consumers) and
F2 (CARRY must verify the live environment). Revision 3 fixes codex-reviewer v2 findings F1 (the
consumer `task_submission_cli._refresh_d6_binding_after_bootstrap` was missing) and F2 (carried
markers conflicted with the `schema_version != 1` rejection at `task_completion.py:755`). Source:
`main` at `d6c1d018`. Defect: #1817 comments c6928715060 and c6928733279. References are `file:line`
at that commit. The revision-3 references in `task_completion.py` and `d6_worktree_env.py` were checked
again at `d9e6f536`. "UNKNOWN" marks facts outside this Task's read paths.

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
  previous record, including the previous marker (`:999`, `:1008-1018`). On success it requires
  `env2_pass_marker.current_head_sha == head` (`:1021-1025`).
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
  (`:987-993`). The byte-equality check is therefore what keeps the build on trusted sources. The
  venv lives in the D6 at `tools/llmtool/.venv/` (`d6_worktree_lifecycle.py:45-46`; publish excludes
  it, `task_advance.py:1927`). The helper never inspects the venv itself; the build report only
  carries `cache_fingerprint` (`d6_worktree_env.py:653-659`, `:726-747`).
- **ENV-2 markers.** The helper writes the PASS marker under the lifecycle lock
  (`_update_env2_pass_marker`, `d6_worktree_env.py:817-909`). The marker has exactly nine keys
  (`:899-909`). The report must be bound to `head_sha` and `tooling_sha` (`:891-898`).
- **Where write paths live.** They are stored only in the Task store record
  (`developer.write_paths`), checked in `tools/llmtool/llmtool/task_store.py:2900-2910`. Callback
  evidence must stay inside them (`task_store.py:3155-3157`). The D6 registry record has no write paths
  (`d6_worktree_lifecycle.py:783-809`). `remote_test._task_path_modules` uses them only to select
  tests (`tools/llmtool/llmtool/remote_test.py:313-323`).
- **Downstream gates.** The remote-test grant is bound to the D6 recorded head
  (`remote_test.py:562-595`). The EC2 runner builds a fresh venv from `git archive <sha>`
  (`remote_test.py:218-231`), so it never uses the D6 venv. The merge gate requires
  `recorded_result_head == result_head` and a clean D6 (`task_advance.py:3246-3265`; snapshot at
  `:2539-2572`).

The code matches the goal's premise, so this is not a SPEC_MISMATCH. The fix stays in the
ENV-2/lifecycle seam plus the completion marker check and its callers, including the OpenCode
dispatch refresh (§3.5). EC2 and the merge gate already follow
the recorded head.

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
  least one, the mode is `CARRY`. The sorted list of allowed changes is `allowed_bootstrap_paths`.

### 3.3 The environment stays on trusted sources (CARRY mode)

In `CARRY` mode, `_invoke_bootstrap` **does not run make** and executes nothing from the Task tree or
the venv. It reads no changed bootstrap source as build input. The D6 keeps the venv from the last
full PASS, which was built from `tooling_sha` sources: by `create_worktree`
(`d6_worktree_lifecycle.py:835`) or by a later `BUILD` record. Before clearing the marker
(`d6_worktree_env.py:965`), the helper runs three checks in order. Each one fails closed.

1. **Historical marker.** The registry marker must pass the strict shape and binding rules of §3.5
   (built or carried), with the same `task_id` and `root_realpath`, the current `tooling_sha`, and
   `current_head_sha` an ancestor of, or equal to, the new head.
2. **Historical report.** `report_sha256` must equal the SHA-256 of the current report file, read
   with the same no-follow, owner-only, `0o600`, single-link rules as `:856-885`. The report must
   hold a well-formed `venv_identity` (§3.4).
3. **Live environment (§3.4).** The current `tools/llmtool/.venv` must match `venv_identity`.

If check 1 or 2 fails, the helper refuses with `ENV2_CARRY_MARKER_UNAVAILABLE`. If check 3 fails or
cannot be completed, it refuses with `ENV2_CARRY_ENVIRONMENT_UNVERIFIED`. It never falls back to
building. Refusal writes no report and leaves the marker alone; the lifecycle rollback (`:1015`)
restores the previous record anyway. The PM handles recovery.

After all three pass, the helper clears the marker. It then writes a report and publishes a carried
marker (§3.5). The report has `schema_version: 2` and the usual binding (`:802-814`), plus
`result: "PASS"`, `status: "env_carried"`, the carried `cache_fingerprint`, `operations: "NONE"`,
`carried_from_head_sha`, `allowed_bootstrap_paths` and the re-verified `venv_identity`.
`carried_from_head_sha` is the head of the last BUILD PASS. On a second CARRY it is copied from the
earlier carried marker, so it always names the head whose sources built the venv.

So the D6 venv never reflects the Task's changed `pyproject.toml`. That is intended: the result is
tested on EC2 in a fresh venv (`remote_test.py:218-231`). Executing the changed sources would require
a Makefile, `worktree_env.py` and pip redesign. That code is outside this Task's read paths, so how
much of it could take trusted inputs is UNKNOWN. CARRY avoids that redesign.

### 3.4 Live venv identity

**Recording.** After every BUILD PASS (`env_created` or `env_reused`), the helper computes
`venv_identity` from the live venv and adds it to the report before `_atomic_bootstrap_report`
(`:1025`). The helper copy that does this is the pinned dispatcher copy, so the venv was just built
from trusted sources. If the venv layout is not as expected, BUILD still passes as today, with
`venv_identity: "UNKNOWN"`. A later CARRY then refuses. This keeps BUILD behaviour unchanged.

`venv_identity = {schema_version: 1, path: "tools/llmtool/.venv", device, inode, uid,
pyvenv_cfg_sha256, interpreter_realpath, interpreter_stat: {device, inode, size, mtime_ns},
site_packages_sha256}`.

**Live check in CARRY** (all must hold, else `ENV2_CARRY_ENVIRONMENT_UNVERIFIED`):

1. `tools/llmtool/.venv` exists, is a directory and not a link, is owned by `os.getuid()`, is not
   group- or world-writable, and its `(st_dev, st_ino)` equals the recorded `device`, `inode`.
2. `.venv/pyvenv.cfg` is a regular file, not a link, single link, owner-only, at most 64 KiB, read
   with `O_NOFOLLOW`. Its SHA-256 equals `pyvenv_cfg_sha256`.
3. `.venv/bin/python` exists. Its `os.path.realpath` equals `interpreter_realpath`. The target is a
   regular executable file that is not group- or world-writable, and its `(st_dev, st_ino, st_size,
   st_mtime_ns)` equals `interpreter_stat`. This catches a removed venv, a replaced venv and an
   upgraded base interpreter.
4. There is exactly one `.venv/lib/python<X.Y>/site-packages`, where `X.Y` matches the
   `cache_fingerprint` version. It has at most 1000 `*.dist-info` directories and none is a link.
   Each `RECORD` is a regular file of at most 1 MiB, read with `O_NOFOLLOW`. `site_packages_sha256`
   is the SHA-256 of the canonical JSON of the sorted `[dist_info_name, sha256(RECORD)]` pairs. It
   must equal the recorded value, so an added, removed or reinstalled package is refused.
5. The recorded `uid` equals `os.getuid()`, and the report `cache_fingerprint` equals the marker's
   `environment_fingerprint`.

**Assumptions and limits.** The standard venv layout (`pyvenv.cfg`, `bin/python`,
`lib/pythonX.Y/site-packages`) is assumed. `worktree_env.py`, which creates the venv, is outside the
read paths (UNKNOWN). The ENV-2 PR must confirm the layout before coding. If the layout differs, the
PR changes the fields but keeps the rule "refuse unless verified". A symlinked `.venv` is refused.
The check does not hash every installed file, so an edit inside a package that leaves its `RECORD`
unchanged is not detected. The race bound is the same single-writer lease as `:962-963`. The D6 venv
is not result evidence: EC2 builds its own venv from the head.

### 3.5 Marker schema and every consumer

**Two exact shapes.** Built markers stay exactly as today: `schema_version: 1` and the nine keys of
`d6_worktree_env.py:899-909`. Existing D6s and BUILD mode are unaffected. A carried marker has
`schema_version: 2` and exactly twelve keys: the nine plus `environment_mode: "CARRIED"`,
`carried_from_head_sha` and `allowed_bootstrap_paths`. `submitted_head_sha` and `current_head_sha`
are the new head. Any other key set, or a version that does not match its key set, is refused.

**Schema matrix.** The key set picks the shape, and the shape fixes every version:

| Shape | Marker `schema_version` | Marker keys | Report `schema_version` | Report `status` | `write_paths` in C4 |
|---|---|---|---|---|---|
| Built (BUILD) | `1` | the nine | `1` (`d6_worktree_env.py:727`); C4 does not read it, as today | `env_created` or `env_reused`; C4 does not read it, as today | ignored |
| Carried (CARRY) | `2` | the twelve | `2`; C4 requires it | `env_carried`; C4 requires it | required |

The D6 registry record itself stays `schema_version: 1` (`d6_worktree_env.py:558-559`); the
`env2_source_allowance` key of §3.1 does not change the record schema. The BUILD report keeps
`schema_version: 1` when it gains `venv_identity` (§3.4); C4 ignores report keys it does not name
(`task_completion.py:816-822`), so existing schema-1 markers and reports stay valid.

**Consumers** (from a search of the read paths for `env2_pass_marker` and
`validate_env2_pass_marker`, plus the caller named by codex-reviewer v2 F1):

| # | Consumer | Today | Change |
|---|---|---|---|
| C1 | `d6_worktree_env._update_env2_pass_marker` (`d6_worktree_env.py:817-909`) | writes the nine-key marker | writes the carried shape in CARRY mode; checks the CARRY report fields (§3.3) in addition to `:891-898` |
| C2 | ENV-2 CARRY, reading the prior marker (new, §3.3 check 1) | none | the C4 shape rules for both shapes and the C4 `task_id`, `root_realpath`, `tooling_sha`, fingerprint and report-hash bindings; the prior `current_head_sha` must be an ancestor of, or equal to, the new head instead of equal to it |
| C3 | `d6_worktree_lifecycle.record_result` (`d6_worktree_lifecycle.py:1021-1025`) | requires a dict with `current_head_sha == head` | unchanged for schema 1. For schema 2 it also requires `allowed_bootstrap_paths` to be a subset of `env2_source_allowance.paths`, with `allowance.head_sha == head`, else `D6_RECORD_RESULT_ENV2_MARKER_INVALID` |
| C4 | `task_completion.validate_env2_pass_marker` (`task_completion.py:740-823`; exact key set `:745-754`; version check `:755`) | refuses any marker that does not have exactly the nine keys, and any `schema_version` other than int `1` | new keyword `write_paths=None`; accepts both exact shapes with the rules below |
| C4a | result collection through `_collect_registered_result_diff` (`task_completion.py:1355-1356`), used by `collect_executor_result` (`:1386-1399`) and `collect_claude_result` (`:1461-1467`) | calls C4 without `write_paths` | passes `write_paths=write_paths` (already a parameter, `:1335`) |
| C4b | `collect_executor_worktree_result` (`task_completion.py:1635-1636`) | calls C4 without `write_paths` | passes `write_paths=write_paths` (already a parameter, `:1601`) |
| C4c | `task_submission_cli._refresh_d6_binding_after_bootstrap` (`task_submission_cli.py:3810-3811`), called by the OpenCode dispatch (`:5009-5012`) after ENV-2 bootstrap | calls C4 without `write_paths` | gains a keyword `write_paths` and passes it to C4. The OpenCode dispatch passes the Task's validated `developer.write_paths` (`task_store.py:2900-2910`), the same list it gives the Developer as write paths |
| C5 | `task_completion.read_d6_registration` (`task_completion.py:650-737`) | keeps the marker as an opaque value (`:737`) and reads only named record keys, so it also accepts `env2_source_allowance` | none |
| C6 | `remote_test.py:563` and `task_advance._d6_snapshot` (`task_advance.py:2556`) | call C5 but never read the marker | none |

`task_store.py` has no marker reference. `task_submission_cli.py` is outside this Task's read paths.
The C4c location and its OpenCode caller come from the codex-reviewer v2 F1 finding at `d9e6f536`;
how the OpenCode dispatch holds the write paths at `:5009-5012`, and whether
`_refresh_d6_binding_after_bootstrap` has other callers, is UNKNOWN here. Other repository files
outside the read paths (for example `tools/llmtool/tests/`, `tools/ec2-runner/`) are also UNKNOWN.
Before coding, PR-2 must search the whole repository for `env2_pass_marker`,
`validate_env2_pass_marker` and `_refresh_d6_binding_after_bootstrap`, and add any further consumer
or caller to this table with the same rule: pass the Task's validated write paths, or rely on the
`None` default below.

**Argument propagation and the compatible default.** The new signature is
`validate_env2_pass_marker(registration, *, expected_root, current_head, write_paths=None)`.

- For a schema-1 marker, `write_paths` is never read. Every caller, changed or not, behaves exactly as
  today for every existing D6.
- For a schema-2 marker, `write_paths` must be a list of strings. If it is `None` or any other type,
  C4 refuses with `D6_ENV2_PASS_MARKER_MISMATCH`. A caller that cannot supply the Task write paths
  therefore fails closed on a carried marker, and never accepts one without the write-path binding.
- C4a, C4b and C4c pass the Task write paths, so all three accept a valid carried marker. Without
  C4c, an OpenCode repair dispatch on a D6 whose recorded head was CARRIED would refuse after
  bootstrap, because ENV-2 then republishes a carried marker for the same head (§3.3).

**C4 rules for both shapes.** This replaces the shape and version checks at `:745-755`. The rest of
`:740-823` is unchanged.

1. Shape (`D6_ENV2_PASS_MARKER_INVALID`, as `:753-754` today): the marker is a dict, and its key set is
   exactly the nine keys (expected version 1) or exactly the twelve keys (expected version 2).
2. Version (`D6_ENV2_PASS_MARKER_MISMATCH`, as `:755` today): `type(schema_version) is int` and
   `schema_version ==` the expected version for the shape. This is the only change to `:755`: the
   literal `!= 1` becomes `!=` the expected version. A nine-key marker with version 2, or a
   twelve-key marker with version 1, is refused.
3. Every other term of `:755-769` applies unchanged to both shapes: `result`, `task_id`,
   `root_realpath`, `submitted_head_sha == current_head_sha == current_head == registration.result_sha`,
   `tooling_sha`, `environment_fingerprint` and `report_sha256`.
4. The report checks of `:772-822` apply unchanged to both shapes: directory and file safety, the
   report hash, `result`, `task_id`, `root_realpath`, `head_sha`, `tooling_sha` and the
   `cache_fingerprint` binding.
5. A schema-1 marker stops here, exactly as today. `write_paths`, the report `schema_version` and the
   report `status` are not checked for it.
6. A schema-2 marker must also pass the carried checks below.

**Carried checks in C4** (schema 2 only; `D6_ENV2_PASS_MARKER_MISMATCH` for every check below):

1. `environment_mode == "CARRIED"`, and `write_paths` is a list of strings.
2. The report has `type(schema_version) is int` and `== 2`.
3. Binding:
   - `registration.env2_approved_tooling_sha is None` and `tooling_sha == registration.source_sha`.
   - `carried_from_head_sha` is a 40-hex string other than `current_head`.
     `registration.source_sha` is an ancestor of it or equal to it. It is a strict ancestor of
     `current_head` (`_is_ancestor`, `:1318`).
   - `allowed_bootstrap_paths` is a non-empty, sorted list of unique strings. Each one is in
     `BOOTSTRAP_SOURCES` and in `write_paths`.
   - `allowed_bootstrap_paths` equals exactly the `BOOTSTRAP_SOURCES` paths that differ in content
     or mode between `tooling_sha` and `current_head` (`_git` `diff --no-renames --name-only -z`
     limited to `BOOTSTRAP_SOURCES`). Every required source exists at `current_head`
     (`_tree_entry`, `:1665`).
   - The report has `status == "env_carried"` and `operations == "NONE"`.
     Its `carried_from_head_sha` and `allowed_bootstrap_paths` equal the marker's. Its
     `venv_identity` is a dict of the §3.4 shape. C4 checks the shape only; the live venv check is
     done by ENV-2 under the lock (§3.3).

The same version pairing binds the other consumers. C1 writes a schema-1 marker with a schema-1 report
in BUILD mode, and a schema-2 marker with a schema-2 report in CARRY mode. C2 applies C4 rules 1-2 to
the prior marker, so it accepts either exact shape. C3 does not read `schema_version` today
(`d6_worktree_lifecycle.py:1021-1025`); it chooses its extra schema-2 check by the twelve-key set and
`schema_version == 2`, and leaves schema-1 markers exactly as today.

`task_completion.py` cannot import the script helper, so it holds its own `BOOTSTRAP_SOURCES`
constant. A test keeps the two copies equal.

### 3.6 EC2 remote test and merge

- **EC2.** After `record_result` succeeds, the existing grant (`remote_test.py:562-597`) binds to the
  recorded head with no change. The runner builds its own venv from the head archive
  (`remote_test.py:218-231`). How `tools/ec2-runner/` hosts that run is UNKNOWN here. Its existing
  trust boundary for PR heads is unchanged.
- **Merge gate.** No code change is needed (`task_advance.py:3246-3265`). A CARRIED marker makes no
  claim about the head's environment. The evidence is the EC2 run, CI and the formal review. After
  merge, the changed source becomes a trusted tooling source only for D6s created from the new `main`.
- **Not fixed here.** `_publish_task` merges `origin/main` before recording (`:1970-1997`). If `main`
  changed a bootstrap source that is not in the Task's write paths, the record still refuses, as today.
  That is a separate defect.

## 4. Ordered PRs

Rule files are the entries in `tools/llmtool/sensitive-paths.json`; `d6_worktree_lifecycle.py`,
`task_advance.py`, `task_completion.py` and `task_submission_cli.py` are listed there
(`sensitive-paths.json:12`, `:20`, `:21`, `:23`) and need Codex security review. The exact test module names are UNKNOWN because `tools/llmtool/tests/`
is outside this Task's read paths. Tests follow the Owner #763 A rule: synthetic `pyproject.toml`
content such as a version bump, synthetic venv fixtures in temporary directories, and no
credential-shaped strings. No production check is relaxed for tests.

Consumers land before the producer, so no carried marker exists until every consumer accepts it.

1. **PR-1 lifecycle binding (rule files: `d6_worktree_lifecycle.py`, `task_advance.py`).**
   - Code: `write_paths` goes through `_record_d6_result` into `record_result`, which writes or removes
     `env2_source_allowance`. C3: the schema-2 check against the allowance.
   - Tests: the allowance is exactly the intersection with `BOOTSTRAP_SOURCES`, bound to the head; an
     empty intersection or the CLI path removes it; rollback restores the previous allowance and
     marker; publish passes the Task write paths; C3 accepts a schema-1 marker as today, accepts a
     schema-2 marker whose paths are inside the allowance, and refuses one with a path outside it or a
     stale allowance head.
   - C5 reads only named keys (`task_completion.py:684-737`), and ENV-2 on current `main` ignores
     unknown record keys (`d6_worktree_env.py:244-283`, `:558-577`). PR-1 alone does not change
     behaviour.
2. **PR-2 completion marker validation (rule files: `tools/llmtool/llmtool/task_completion.py`,
   `tools/llmtool/llmtool/task_submission_cli.py`).**
   - Code: C4 with the `write_paths=None` keyword, the shape and version rules and the carried checks
     of §3.5; C4a and C4b pass `write_paths`; C4c: `_refresh_d6_binding_after_bootstrap` gains a
     `write_paths` keyword and the OpenCode dispatch (`task_submission_cli.py:5009-5012`) passes the
     Task's validated `developer.write_paths`; the `BOOTSTRAP_SOURCES` constant. First, the
     repository-wide consumer and caller search of §3.5.
   - Tests: `validate_env2_pass_marker` unit tests. The existing schema-1 tests pass unchanged. A
     schema-1 marker is accepted with `write_paths` omitted, `None` or a list that excludes every
     bootstrap source, and its report is accepted with `schema_version: 1` with or without
     `venv_identity`. A valid carried marker and report are accepted. Each of these is refused with
     the code §3.5 states: an extra or missing key (`INVALID`); a nine-key marker with version 2 or a
     twelve-key marker with version 1 (`MISMATCH`); a carried marker with `write_paths` omitted or
     `None`; a carried report with `schema_version: 1`; a wrong `environment_mode`;
     `carried_from_head_sha` equal to the head or not an ancestor; approved tooling; a path outside
     `BOOTSTRAP_SOURCES` or outside `write_paths`; unsorted or duplicate paths; paths that differ from
     the actual tooling-to-head bootstrap diff; a missing required source; a report with the wrong
     `status`, `operations` or carried fields; and a malformed `venv_identity`.
   - **Regression test for C4c (codex-reviewer v2 F1):** `_refresh_d6_binding_after_bootstrap`, run
     through the OpenCode dispatch path with ENV-2 bootstrap mocked or injected (no real bootstrap,
     no credential-shaped data), accepts a synthetic D6 that holds a valid carried marker when the
     Task write paths include the changed bootstrap source. A schema-1 marker gives the same result as
     today. The test asserts the write paths reach C4, so a later caller that drops them fails. When
     the changed path is not in the write paths, the test checks each boundary with its own code
     (codex-reviewer v3 F1); both existing error contracts and the mapping stay as they are, and this
     design proposes no production error-contract change:
     - At the OpenCode dispatch boundary it expects `OPENCODE_ENV2_MARKER_INVALID`.
       `_refresh_d6_binding_after_bootstrap` maps a C4 refusal to that code
       (`task_submission_cli.py:3812-3813`, reached from the dispatch at `:5012`, as cited by the
       finding; that file is outside this Task's read paths).
     - At the C4 consumer, a direct `validate_env2_pass_marker` call with the same synthetic
       registration, head and write paths refuses with `D6_ENV2_PASS_MARKER_MISMATCH`.
   - **Result-collection tests with the new marker:** `collect_executor_result`,
     `collect_claude_result` and `collect_executor_worktree_result` each run on a synthetic registered
     D6 whose head changes `tools/llmtool/pyproject.toml` (a version bump) and that holds a carried
     marker and report. They return the result. The same fixture with the path removed from
     `write_paths` refuses with `D6_ENV2_PASS_MARKER_MISMATCH`. A test asserts the
     `BOOTSTRAP_SOURCES` constant equals the helper's set.
   - No carried marker is produced yet, so behaviour on `main` does not change.
3. **PR-3 ENV-2 acceptance, CARRY and venv identity (`tools/llmtool/scripts/d6_worktree_env.py`; not
   listed in sensitive-paths.json; security review recommended).**
   - Code: §3.2, §3.3 and §3.4, and C1 and C2. First, confirm the venv layout in `worktree_env.py`.
   - Tests: an allowed `pyproject.toml` change gives CARRY with the carried marker and report, and make
     is not called; BUILD reports now include `venv_identity`, and BUILD is otherwise unchanged;
     `D6_BOOTSTRAP_SOURCE_MISMATCH` is kept for a path outside the allowance, a stale allowance head,
     approved tooling, bytes differing from the head blob, a group-writable mode, a deleted required
     source and an optional input added without allowance; a missing or mismatched prior marker or
     report gives `ENV2_CARRY_MARKER_UNAVAILABLE`; `ENV2_CARRY_ENVIRONMENT_UNVERIFIED` for a missing
     venv, a replaced venv directory (new inode), a symlinked venv, a changed `pyvenv.cfg`, a changed
     interpreter target or stat, an added or removed dist-info, a changed `RECORD`, an oversized file,
     and a prior report with `venv_identity` absent or `"UNKNOWN"`; a second CARRY keeps the original
     `carried_from_head_sha`.
   - **End-to-end test:** `record_result` with write paths that include `tools/llmtool/pyproject.toml`
     records a CARRY result, C3 accepts it, and the PR-2 collection functions and
     `_refresh_d6_binding_after_bootstrap` (C4c) accept the resulting marker for the same D6.
   - **Bootstrap blocker:** this PR changes the helper, which is itself a bootstrap source
     (`:503-511`). Under current `main`, its own `record_result` fails with this same defect. The
     delivery route needs an Owner decision through the PM (UNKNOWN). D6s created before PR-3 have no
     `venv_identity`, so they cannot CARRY until a fresh BUILD record; that is the fail-closed outcome.
4. **PR-4, optional (rule file: `tools/llmtool/sensitive-paths.json`).** Add the
   `BOOTSTRAP_SOURCES` paths so that changes to them also need security review. This needs an
   Owner decision. Test: the sensitive-path classification test for the new entries.

Once PR-1 to PR-3 are on `main` and the dispatcher is pinned to them, a Task such as the #1817
`pyproject.toml` change can record its result, pass result collection, get an EC2 grant, open a PR and
merge through the existing gates.
