# A13.1 design: project registration and trust

Status: **proposed design. Nothing marked "proposed" is implemented.**
Tracking: #2793. Docs only; this note grants no authority.

## 1. Scope and reading rules

Covered: the project registry, stable repository identity, the
project-scoped trust file and policy, explicit migration of legacy
self-development, and their negative cases. Other A13.1 or later work is
outside this note.

Linked sources, not repeated here:

- [ROADMAP](../../tools/llmtool/ROADMAP.md), section A13 (milestone
  A13.1, validation and merge policy, authorization boundaries).
- [Legacy trust.json design](./a13-trust-json-design-369.md): the
  single-repository file, its read algorithm, threat analysis and
  rotation. It lists multiple repositories as a non-goal, so everything
  project-scoped below is new.
- [ProjectContext consumer handoff](./a13-project-context-consumer-handoff.md).

Reading rules:

- "Existing" statements were read from `tools/llmtool/llmtool/trust.py`
  and `tools/llmtool/llmtool/project_context.py` at
  `30adfab962aae6a029c063ce872709444f46fcda`. They say what those two
  files do, not who calls them.
- "Proposed" marks new behaviour, file formats, command names and codes.
- Sections 2 to 5 state what an accepted action requires. What is refused,
  and with which code, is in [section 6](#6-negative-cases-and-refusal-codes).
- Open questions (`O1` to `O12`) are in [section 7](#7-open-questions).
- A real write to an external repository or to GitHub needs separate Owner
  authorization. Registration, migration, a trust file and a policy value
  never supply it. The form of that record is open (O5).

## 2. Project registry

### 2.1 Project key

Existing: `_validate_project_id` in `project_context.py` accepts any
printable ASCII string of 1 to 100 characters. That includes `/`, `\`,
`.`, `..` and spaces, so passing it does not make a value safe as a path
component.

Proposed: a **project key** is the only value accepted as a `project_id`
for registration, selection and storage.

- The value is exactly a `str` and fully matches
  `[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?` (1 to 64 characters). The match
  covers the whole string (`fullmatch`), so a trailing newline is not
  accepted.
- The raw input is checked. It is never trimmed, case-folded,
  Unicode-normalized, escaped or otherwise rewritten into an accepted key.
- This syntax is narrower than the existing validator, so every key also
  passes it.

Order, for each command and each key read from a file:

1. Check the syntax of the key.
2. Check for collisions: against the keys in the registry and, during
   registration or migration, against the entry names listed in the fixed
   directory `projects/`. A new key must not equal any of them after ASCII
   case folding. An existing project's directory entry must equal its key
   byte for byte, with no other entry that case-folds to it.
3. Only then join the key to a path.

The case-folded comparison covers entries created outside llmtool on a
case-insensitive filesystem.

### 2.2 Storage

Proposed layout, under the account home that `load_trust` obtains from the
password database:

```text
<home>/.config/llmtool/
  trust.json                    legacy file, format unchanged
  registry.json                 proposed: project index and migration marker
  registry.lock                 proposed: writer lock
  projects/<key>/trust.json     proposed: project-scoped trust and policy
```

Proposed `registry.json`, with exactly these keys at each level:

```json
{
  "schema_version": 1,
  "projects": {"<key>": {"trust_sha256": "<hex>"}},
  "legacy_migration": null
}
```

- `trust_sha256` is the SHA-256 of the raw bytes of that project's trust
  file. All other project data lives only in the trust file.
- `legacy_migration` is defined in section 5.2.
- The registry is absent only before the first successful registration or
  migration, when `projects/` is also absent or empty.

Existing: `_read_trust_bytes` opens only the file `trust.json` in
`.config/llmtool`, and applies the owner-only mode `0700` rule to the last
directory only.

Proposed: one shared reader takes the directory components and file name.
It keeps the existing no-follow walk, `.git` ancestor check, file mode,
link count, size and change detection, and the parsing steps of
`load_trust` (BOM, strict UTF-8, nesting depth, duplicate keys, non-finite
constants). It applies the `0700` rule to `llmtool`, `projects` and
`<key>`. Both new files use the existing 16 KiB cap (O12).

### 2.3 Writing

Proposed:

- A writer holds an exclusive lock on `registry.lock` (owner-only regular
  file, opened without following links) from reading the registry until its
  commit.
- Files are written to a new exclusive temporary file with mode `0600` in
  the destination directory, synced, renamed into place, and the directory
  is synced.
- A new project's directory is created exclusively with mode `0700` and its
  trust file is written first. Replacing `registry.json` is the single
  commit point.
- An unreferenced `projects/<key>/` directory is an orphan. llmtool neither
  adopts nor deletes it. The user removes it manually; while a valid
  registry exists, choosing another key is also possible.

### 2.4 Registration

Proposed command name: `llmtool project register`.

1. Apply the key order of section 2.1 while holding the writer lock.
2. Take every trust file value from explicit user input. Read-only
   discovery may suggest a value; repository content cannot set or approve
   one.
3. Validate the content (section 4, including construction of a
   `ProjectContext`), the repository identity (section 3.2), uniqueness
   (section 3.3) and the legacy boundary (section 5.1).
4. Show the proposed file and require explicit confirmation.
5. Repeat the checks of step 3, then write as in section 2.3.

Registration writes only under `<home>/.config/llmtool`. The three roots
must already exist.

### 2.5 Selection

Proposed:

- Each project-scoped command names its project key explicitly. There is no
  default project and no inference from the current directory, origin URL,
  slug or environment, even when only one project is registered.
- Selection reads the registry, then the selected trust file. The file
  digest must equal the registry's `trust_sha256`, and the file's
  `project_id` must equal the selected key.
- Every path, repository, Task, worktree record and evidence item used by
  the operation must belong to that project.

## 3. Stable repository identity

### 3.1 What exists

`verify_repository` in `trust.py` requires that `canonical_root` is the Git
top level, that both Git directory queries equal `<canonical_root>/.git`,
and that the output of `remote get-url origin` is in `allowed_origins`. It
asks for one fetch URL only: it does not check push URLs or further URLs.
It does not check a GitHub repository ID or the identity of the filesystem
objects.

`_SLUG` accepts mixed case and `_validate_schema` restricts origins to two
`github.com` forms built from the slug.

### 3.2 Proposed identity

| Part | Role | Source at registration |
|---|---|---|
| Host (`github.com`) and numeric repository ID | The identity of the repository | Authenticated GitHub query |
| `slug`, `allowed_origins` | Locators that must still point to that identity | User input, confirmed against the same query |
| Device and inode of `canonical_root` and of its `.git` directory | Identity of the local checkout | `stat` without following links |

Each mutating operation, local or remote, checks immediately before acting:

1. the checks of `verify_repository`;
2. every URL from `remote get-url --all origin` and from
   `remote get-url --push --all origin` is in `allowed_origins`;
3. both recorded device and inode pairs match (O2);
4. a fresh GitHub query for the slug returns the recorded repository ID and
   the same slug, compared ignoring ASCII case;
5. the authenticated GitHub user matches `github_identity` (section 4.2).

No result is cached or inferred from an earlier operation (O3). These
checks do not establish how local Git configuration routes the transport
(O4).

### 3.3 Uniqueness across projects

Proposed, checked against every registered trust file at registration and
migration:

- no two projects share a repository ID, or a slug ignoring ASCII case;
- the `canonical_root` and `workspace_root` of one project are neither equal
  to, inside, nor containing those of another;
- the same relation is checked on device and inode along each root's
  ancestors, because a string comparison of resolved paths is not evidence
  that two spellings name different directories.

## 4. Project-scoped trust file and policy

### 4.1 Compatibility with existing code

- The project file is a separate format from the legacy file, whose key
  set is closed (section 5.2).
- `_validate_roots` in `trust.py` requires `tasks_root` to be a strict child
  of `workspace_root`. `_validate_roots` in `project_context.py` requires
  all three roots to be pairwise non-overlapping. Roots accepted by the
  first are therefore rejected by `ProjectContext.from_trust` (O1).
- `ProjectContext.from_trust` takes a mapping with exactly `canonical_root`,
  `workspace_root`, `tasks_root` and `repository`, and requires lists.
  `load_trust` returns a `TrustConfig` whose lists are tuples. A proposed
  adapter builds that four-key mapping with lists.
- `load_trust` keeps one process-wide cache, and its `home` argument is an
  internal test seam. The proposed project loader neither shares that cache
  nor redirects `home`; it reads the selected project's file for each
  operation.

### 4.2 Proposed format

Location: see section 2.2. Keys are exact at each level; only
`repository.approval_refs` may be omitted. Values below are placeholders.

```json
{
  "schema_version": 1,
  "project_id": "<key>",
  "repository_identity": {"host": "github.com", "repository_id": 1},
  "root_identity": {
    "canonical_root": {"device": 1, "inode": 1},
    "git_dir": {"device": 1, "inode": 2}
  },
  "canonical_root": "<CANONICAL_ROOT>",
  "workspace_root": "<WORKSPACE_ROOT>",
  "tasks_root": "<TASKS_ROOT>",
  "repository": {"slug": "<owner>/<name>", "allowed_origins": ["<origin>"]},
  "target_branch": "<branch>",
  "github_identity": {"login": "<login>", "user_id": 1},
  "permissions": {
    "worktree_write": false,
    "branch_push": false,
    "pull_request_write": false,
    "issue_write": false,
    "merge": false
  },
  "ci_trust": null,
  "tools": {"git": null, "gh_path": null, "lsof": null}
}
```

| Field | Proposed rule |
|---|---|
| Numeric IDs, devices, inodes | `int`, not `bool`; IDs are positive, devices and inodes non-negative |
| Three roots | Existing rules of `_validate_schema` and `_validate_roots` in `trust.py`, subject to O1 |
| `repository` | Existing slug and origin rules; `approval_refs`, when present, follows the existing rule. When absent, no approval reference is configured |
| `target_branch` | 1 to 200 characters matching `[A-Za-z0-9][A-Za-z0-9._/-]*` and accepted by `git check-ref-format --branch`. A pull request base or merge target must equal it |
| `github_identity` | `login` matches `[A-Za-z0-9][A-Za-z0-9-]{0,38}`; both values match the authenticated user (O11). No credential is stored |
| `permissions` | Each value is a `bool`; only `true` permits that class of operation |
| `ci_trust` | `null`, or an object under the existing rules, including at least one code check and one docs check. `null` supplies no CI evidence (O6) |
| `tools` | Existing rules of `_validate_schema` and `_validate_tools` |

### 4.3 Policy

Proposed:

- `permissions` is an upper bound. An allowed operation also needs every
  other applicable gate and, for a real external write, the authorization
  in section 1.
- The mandatory merge floor is in the ROADMAP. The format has no key that
  disables or weakens it.
- Policy comes only from this file. Target repository content is untrusted.
- Required validation checks and manual acceptance are not configured by
  this format (O6). No operation may treat their absence as a passed check.
- An existing project's file is not changed by any designed operation (O7).

## 5. Legacy self-development migration

### 5.1 Before migration

Proposed:

- Existing legacy entry points keep their current gates and read the legacy
  file. Project-scoped commands never read it as a substitute for a project
  trust file.
- A legacy entry point proceeds only when either the registry is absent with
  `projects/` absent or empty, or a valid registry has `legacy_migration`
  equal to `null`.
- While a legacy file exists and is not migrated, ordinary registration must
  load it successfully and prove that the new project is separate from it:
  different repository ID (resolved from the legacy slug) and slug, and
  roots separate as in section 3.3. The legacy repository enters the
  registry only through migration.

Which entry points call `load_trust` or still use built-in anchors was not
verified; migration depends on each one applying these rules (O8).

### 5.2 Migration marker

Existing: `_validate_schema` in `trust.py` requires the legacy file to have
exactly seven top-level keys: `schema_version`, `canonical_root`,
`workspace_root`, `tasks_root`, `repository`, `ci_trust` and `tools`. A
marker added as a key would make the file invalid.

Proposed: **the marker lives only in `registry.json`**, in
`legacy_migration`. The legacy file is not edited, renamed or deleted.

```json
"legacy_migration": {"project_id": "<key>", "legacy_trust_sha256": "<hex>"}
```

- Both keys are required and no others are allowed. `project_id` names a
  record in `projects`.
- `legacy_trust_sha256` is the digest of the legacy bytes that were
  migrated.
- The marker and the project record are committed by the same registry
  replacement.
- A build that predates the marker does not consult it, and removing both
  the registry and `projects/` removes it (O10).

### 5.3 Migration steps

Proposed command name: `llmtool project migrate-legacy`, run explicitly
with a user-chosen key. No other command triggers it.

1. Apply the key order of section 2.1 while holding the writer lock, and
   require that no marker is set.
2. Load the legacy file with every check of `load_trust`, then run
   `verify_repository`.
3. Copy the values of `canonical_root`, `workspace_root`, `tasks_root`,
   `repository`, `ci_trust` and `tools` unchanged, converting tuples to
   lists. The legacy `schema_version` and binding digest are not carried
   over.
4. Take `target_branch`, `github_identity` and `permissions` from explicit
   user input, with no defaults. Obtain the identity parts as in section 3.2.
5. Validate as registration does (section 2.4, step 3, except the legacy
   separation rule).
6. Show the proposed file and require explicit confirmation.
7. Read the legacy bytes again, bypassing the process cache. Their digest
   must equal the one from step 2. Repeat step 5.
8. Write as in section 2.3, setting the marker of section 5.2.

Interruption before the commit leaves the marker unset and may leave an
orphan (section 2.3), whose effect on legacy entry points follows section
5.1. Adoption of existing worktree records and Task state is open (O9).
Rollback of a committed migration is open (O7).

## 6. Negative cases and refusal codes

All behaviour here is proposed. Code type **E** is a name that
`trust.py` or `project_context.py` already raises, applied as proposed to
the new action; **P** is a proposed new name. A refusal reports the code
only, without paths or values, commits nothing and has no fallback to
another project, the legacy file or built-in values. An interrupted write
may leave only the orphan or temporary file of section 2.3.

### 6.1 Key and registry

| ID | Case | Code |
|---|---|---|
| N1 | Key is not a `str`, empty, longer than 64, or contains `/`, `\`, `.`, space, uppercase, underscore, control or non-ASCII characters, a leading or trailing `-`, or a trailing newline. Refused before any project path is derived | `PROJECT_KEY_INVALID` (P) |
| N2 | New key equals a registered key or a `projects/` entry after case folding, including an orphan; or an existing project's entry differs in case or has a case-folded twin | `PROJECT_KEY_COLLISION` (P) |
| N3 | Registry absent for a project-scoped operation, or absent while `projects/` has entries (legacy entry points included) | `PROJECT_REGISTRY_MISSING` (P) |
| N4 | Registry has wrong keys or types, a project key failing section 2.1, or a marker naming no record (duplicate JSON keys fall under N5) | `PROJECT_REGISTRY_INVALID` (P) |
| N5 | Unsafe, oversized, malformed or changing registry or trust file, or an unsafe directory | the matching `TRUST_*` reader or parser code (E) |
| N6 | Writer lock cannot be safely opened or acquired | `PROJECT_REGISTRY_LOCKED` (P) |
| N7 | No project key supplied | `PROJECT_SELECTION_REQUIRED` (P) |
| N8 | Well-formed key with no record | `PROJECT_NOT_REGISTERED` (P) |

### 6.2 Trust file, policy and cross-project use

| ID | Case | Code |
|---|---|---|
| N9 | Registered trust file is missing | `TRUST_MISSING` (E) |
| N10 | File digest differs from the registry, including after a manual edit | `PROJECT_TRUST_DIGEST_MISMATCH` (P) |
| N11 | `project_id` differs from the selected key; or a path, repository, Task, record or evidence item belongs to another project, even with equal issue or branch numbers | `PROJECT_CONTEXT_MISMATCH` (P) |
| N12 | Wrong schema version | `TRUST_SCHEMA_VERSION_UNSUPPORTED` (E) |
| N13 | Missing or extra key or invalid value, including a policy key that tries to relax the merge floor | `TRUST_SCHEMA_INVALID`, `TRUST_CI_INVALID` or `TRUST_TOOL_INVALID` (E) |
| N14 | Root fails the existing file or overlap rules within one project | `TRUST_PATH_INVALID` or `TRUST_PATHS_OVERLAP` (E) |
| N15 | `ProjectContext` rejects the mapping, which currently includes every trust-valid root layout (O1) | `PROJECT_CONTEXT_INVALID` (E) |
| N16 | Operation's permission is `false` or the operation has no defined permission | `PROJECT_PERMISSION_DENIED` (P) |
| N17 | Real external write without verifiable Owner authorization; currently each one (O5) | `PROJECT_EXTERNAL_WRITE_UNAUTHORIZED` (P) |
| N18 | Pull request base or merge target differs from `target_branch` | `PROJECT_TARGET_BRANCH_MISMATCH` (P) |
| N19 | Operation needs an approval reference or CI evidence that is not configured, or a validation policy that is not designed | `PROJECT_POLICY_UNCONFIGURED` (P) |
| N20 | Request to update, re-validate or unregister a project, or to roll back a migration (O7) | `PROJECT_OPERATION_UNSUPPORTED` (P) |

### 6.3 Repository identity

| ID | Case | Code |
|---|---|---|
| N21 | Wrong top level, linked worktree or separate Git directory as root, or any fetch or push origin outside `allowed_origins` | `TRUST_REPOSITORY_INVALID` (E) |
| N22 | Device or inode differs (re-cloned, moved or replaced checkout) | `PROJECT_ROOT_STALE` (P) |
| N23 | Observed repository ID or slug differs (rename, transfer, or deletion and re-creation under the same name) | `PROJECT_IDENTITY_STALE` (P) |
| N24 | GitHub query fails, is unauthorized, times out, is offline or returns incomplete data | `PROJECT_IDENTITY_UNVERIFIED` (P) |
| N25 | Authenticated user differs from `github_identity` | `PROJECT_GITHUB_IDENTITY_MISMATCH` (P) |
| N26 | Repository ID, slug or roots collide with another project, by string or by device and inode | `PROJECT_IDENTITY_COLLISION` (P) |
| N27 | Another registered file prevents the uniqueness check | `PROJECT_UNIQUENESS_UNVERIFIED` (P) |

### 6.4 Migration

| ID | Case | Code |
|---|---|---|
| N28 | Legacy file missing at migration; or an existing legacy file that is invalid or fails repository verification, at migration or during the separation check of ordinary registration | the matching `TRUST_*` code (E) |
| N29 | Ordinary registration targets, or overlaps, the unmigrated legacy repository | `PROJECT_LEGACY_MIGRATION_REQUIRED` (P) |
| N30 | Migration requested when a marker is already set; or a legacy entry point runs after it | `PROJECT_LEGACY_ALREADY_MIGRATED` (P) |
| N31 | Legacy bytes change between load and commit | `TRUST_CHANGED_DURING_RUN` (P; name taken from the legacy design) |
| N32 | A required new value or the confirmation is absent | `PROJECT_INPUT_REQUIRED` (P) |
| N33 | Migration requested before the entry point inventory is complete (O8) | `PROJECT_OPERATION_UNSUPPORTED` (P) |
| N34 | Project-scoped mutation of a worktree record or Task state with no project binding (O9) | `PROJECT_RECORD_UNBOUND` (P) |

## 7. Open questions

Each is **open**. Where it affects an action, the action is refused until a
reviewed decision replaces that refusal.

| ID | Question | Until decided |
|---|---|---|
| O1 | Which root rule applies (section 4.1)? Recommendation: change `ProjectContext` to require `canonical_root` separate from `workspace_root` and `tasks_root` strictly inside it, which keeps the legacy layout. This changes behaviour the handoff note passes on unchanged, so it needs a recorded decision | No registration or migration (N15) |
| O2 | Are device numbers stable across reboot and remount on each supported platform? | A mismatch refuses (N22) |
| O3 | May identity results be cached or local mutations run offline, and how is the interval between check and remote write closed? | No cache or offline mutation (N24); the interval is an unclosed risk that must be decided before external writes are enabled |
| O4 | Which local Git settings can redirect transport despite matching URLs, and how are they excluded? | External writes stay refused |
| O5 | What record proves Owner authorization for an external write, and its scope and expiry? | Refused (N17) |
| O6 | Format of required checks, execution modes and manual acceptance, and whether `ci_trust` may omit a code or docs check (A13.2) | Dependent operations refused (N19) |
| O7 | Design of trust update and re-validation, unregistration, key reuse and migration rollback | Refused (N20); stale or mismatched projects stay refused |
| O8 | Which entry points consume legacy trust or built-in anchors, and do embedded copies of the existing reader block exist that a shared reader must change? | Migration refused (N33) |
| O9 | How are existing worktree records and Task state bound to the migrated project, including legacy digest stamps? | Refused (N34) |
| O10 | Does the marker need a second anchor against older builds or removal of the registry? | Downgrade or removal is unsupported; no claim that the marker prevents it |
| O11 | Are GitHub App or bot identities supported? | Not accepted (N13) |
| O12 | Is 16 KiB enough for the registry, and what do macOS ACLs and filesystem case behaviour require? macOS behaviour is unverified | Oversize refuses (N5); no macOS support claim |
