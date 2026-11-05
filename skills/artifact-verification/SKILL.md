---
name: artifact-verification
description: Verify JSON schemas, path constraints, role constants, external IDs, and evidence labels before publishing technical reports, runbooks, or handoff artifacts. Use when drafting or reviewing reports, HostPin/contract examples, or readiness handoffs.
---

# Artifact Verification

Validate structured examples, paths, identifiers, and evidence labels against authoritative repository sources when preparing technical artifacts for publication.

## Process

1. **Read complete parser and constant definitions from source:**
   - Do not infer wire/JSON schemas from dataclass fields, partial line slices, or `<CONTEXT_SUMMARY>`.
   - Read `task_verification.py` when verifying Task Contract acceptance evidence and its result statuses.
   - Read the complete parser function (for example, `_decode_host` in `tools/llmtool/llmtool/review_authority.py` or `parse_task_contract` in `tools/llmtool/llmtool/task_verification.py`) and grep authoritative constants (such as `REVIEWER_ROLES` in `tools/llmtool/llmtool/task_review.py`).
2. **Separate `TEMPLATE_ONLY` blocks from `SYNTHETIC_PARSER_VERIFIED` fixtures:**
   - Label any example containing `<...>` placeholders or unprovisioned paths as `TEMPLATE_ONLY` and state the actual failure point: a template the parser rejects fails schema validation, while a well-formed but unprovisioned path can pass static parsing and fail only at runtime.
   - When claiming an example is valid, test a concrete disposable synthetic fixture (mode `0600` inside a `0700` temporary directory outside the repository) against the real CLI or parser:
     ```sh
     tools/llmtool/.venv/bin/llmtool review-authority check --host /private/tmp/<0700-dir>/synthetic-host.json --json
     ```
   - Record the concrete fixture SHA256, exact command, exit code, output, and parser-only boundary limits (static structural validation does not open SQLite ledgers, check `auth_home`, execute binaries, or grant live authority).
3. **Validate filesystem paths against actual path and inode rules:**
   - Verify candidate paths against `action_contract._path`. It accepts non-empty, control-free NFC text up to 1,024 UTF-8 bytes with slash-separated components (at most 32). Relative paths cannot start or end with `/`; absolute paths must start with `/` and cannot be only `/`. Components cannot be empty, `.` or `..`, start with `-`, equal a denied credential/Git name, start with `.env.`, or end with `.pem`, `.key`, `.p12` or `.pfx`. It also rejects `.github/workflows` and `.config` followed by `llmtool`, `gh` or `gcloud`. Remaining component characters must be Unicode alphanumeric, `.`, `_` or `-`. `task_verification.py` applies these Task path rules through `action_contract._path`.
   - When checking filesystem entries, account for the applicable file validator's `O_NOFOLLOW` and `stat.S_ISREG` checks against symlinks and non-regular files.
   - State operational and upgrade caveats when documenting conditional provisioning options (such as hardlink or file-copy drift after package upgrades).
4. **Preserve pinned evidence and verify unrecorded external IDs:**
   - Reuse existing timestamped, SHA-pinned verification evidence without re-running unchanged tests or queries.
   - If a specific external identifier (such as a GitHub Actions Run `databaseId`, PR/Issue comment URL, `st_dev`/`st_ino`, or SHA256 digest) is not already preserved in the pinned record, query it once from the authoritative source or mark it `NOT_QUERIED`. Never guess or reconstruct numeric IDs from memory.
5. **Keep evidence and model provenance labels distinct:**
   - Distinguish `TEMPLATE_ONLY`, `SYNTHETIC_PARSER_VERIFIED`, `SOURCE_VERIFIED`, `FAKE_TEST_VERIFIED`, `HELP_VERIFIED`, `USER_VERIFIED`, `LIVE_NOT_VERIFIED / NOT_AUTHORIZED`, and `INDEPENDENT_REVIEW_PASS`.
   - Distinguish `USER_PROMPT_DECLARED` model names from `MODEL_ID_NOT_VERIFIED` when the runtime does not expose the active model identifier, and separate `0` delegated Worker/provider calls in local probes from the outer CLI session.

## Rules

- Skills and checklists reduce risk; they never substitute for running the real parser or required CI/review gates.
- Never claim `PASS` or `VERIFIED` for an unexecuted template or an unverified live runtime step.
- Do not read or copy live credentials, or create production Host/ledger/auth-HOME state, during documentation or parser verification.

## Completion criteria

- Every schema, role constant, and path in the artifact matches the authoritative source parser.
- `TEMPLATE_ONLY` snippets and `SYNTHETIC_PARSER_VERIFIED` fixtures are clearly separated, with exact SHA256, command, exit code, and boundary limits recorded for verified fixtures.
- External IDs and digests are either pinned from prior verified evidence, freshly queried once, or explicitly marked `NOT_QUERIED`.
- Unexecuted runtime steps and unauthorized live states are labeled `LIVE_NOT_VERIFIED / NOT_AUTHORIZED`.
