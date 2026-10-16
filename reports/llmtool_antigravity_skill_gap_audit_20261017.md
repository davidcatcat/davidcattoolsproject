# Antigravity CLI (`agy 1.4.0`) Skills Gap Audit & M2 Live Readiness V2 Correction

- **Task IDs:** `ISSUE120-AGY-SKILL-GAP-AUDIT-V1` / `ISSUE120-M2-AGY-LIVE-READINESS-V2`
- **Tracking Issue:** [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)
- **Date:** 2026-10-17
- **Base `main` SHA:** `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39`
- **Runtime / Model Provenance:**
  - **Host CLI:** Antigravity CLI `1.4.0` (`/Users/davidsun/.local/bin/agy`, macOS arm64)
  - **Prompt-Declared Model (`USER_PROMPT_DECLARED`):** `Gemini 4 Argon Medium` (audit & R1 revision turns) / `Claude Sonnet 5.5 Medium` (prior V1 readiness turn)
  - **Execution Model Identity (`MODEL_ID_NOT_VERIFIED`):** Active underlying model identity and live token counters are `MODEL_ID_NOT_VERIFIED` (not exposed inside the agent tool environment; author-observed subagent transcripts record `MODEL_PLACEHOLDER_M418` / `MODEL_TIER_INHERIT`, which does not establish actual model identity).
- **Scope:** Documentation and synthetic parser verification only (`0` delegated Worker/provider calls in local probes, `0` credential reads/copies, `0` production Host/ledger/auth-HOME installations, `0` runtime code changes; the AI-authored audit session itself runs on the host CLI). Production Host, Ledger, Auth HOME, CLI runtime execution, and live model inference remain **`LIVE_NOT_VERIFIED / NOT_AUTHORIZED`**.

---

## Part I — `ISSUE120-AGY-SKILL-GAP-AUDIT-V1`

### 1. Repository Norms & Skills Inventory

#### 1.1 Core Guidance Documents
| Document | Lines / Bytes | Scope & Role | `agy 1.4.0` Auto-Load Status |
|---|---|---|---|
| `AGENTS.md` | 200 lines / 9,683 B | AI team delivery authority, canonical Mac path, fixed worktree exception, runtime isolation, safe-git, and quality gates | **`AUTO_LOADED` (`LOCAL_VERIFIED`)**: Injected automatically into `<user_rules>` (`always_on`, below the 24,000-byte per-file rule cap) |
| `CLAUDE.md` | 139 lines / 8,604 B | Claude collaboration contract, review output rules, and skill design rules (small, composable, non-duplicative, checkable completion criteria) | **`NOT_AUTO_LOADED` (`LOCAL_VERIFIED`)**: Not loaded automatically by `agy`; read only when `view_file` is invoked |
| `tools/llmtool/ROADMAP.md` | 1,366 lines / 105,659 B | Single authoritative A0–A12 product roadmap and gate status table | On-demand (linked from `AGENTS.md`) |
| `tools/llmtool/WORKFLOW.md` | 1,814 lines / 131,919 B | Cloud/Mac workflow, work-package delivery, and M2 Foreground Reviewer Worker (`llmtool-worker/claude-cli`) contract | On-demand (linked from `AGENTS.md`) |

#### 1.2 Shared Repository Skills (`skills/<name>/SKILL.md`)
All 12 repository skills live under top-level `skills/<name>/SKILL.md` (with `skills/<name>/agents/openai.yaml` for Codex):

| # | Skill Name | Path | Lines / Bytes | Sibling Files | Trigger & Purpose Summary | Auto-Discovered by `agy 1.4.0` Today? |
|---|---|---|---|---|---|---|
| 1 | `ai-team-delivery` | `skills/ai-team-delivery/SKILL.md` | 323 / 23,340 B | `agents/openai.yaml` | Owner/Coordinator/Developer/Reviewer split, P0 escalation rule, Issue #92 defect triage (`SEV-0..3`), work-package delivery | **No** |
| 2 | `safe-git` | `skills/safe-git/SKILL.md` | 458 / 29,118 B | `preflight.py`, `test_preflight.py`, `agents/openai.yaml` | Single canonical checkout claim/release, two fixed developer worktrees, safe commit/push/PR and `preflight.py` checks | **No** |
| 3 | `inspect-repo` | `skills/inspect-repo/SKILL.md` | 39 / 2,659 B | `agents/openai.yaml` | Pre-change map of status, docs, schemas, analogous code, and tested revision provenance | **No** |
| 4 | `implement-feature` | `skills/implement-feature/SKILL.md` | 62 / 3,428 B | `agents/openai.yaml` | Vertical slice implementation, public-seam tests, doc vs app validation, specialist gate routing | **No** |
| 5 | `review-change` | `skills/review-change/SKILL.md` | 72 / 4,513 B | `agents/openai.yaml` | Ordinary diff review, capability vs permission boundary, secret redaction, evidence separation | **No** |
| 6 | `bug-hunt` | `skills/bug-hunt/SKILL.md` | 68 / 3,966 B | `agents/openai.yaml` | Active search for reproducible correctness, concurrency, resource, or state defects | **No** |
| 7 | `security-review` | `skills/security-review/SKILL.md` | 78 / 4,564 B | `agents/openai.yaml` | Threat modeling, trust boundaries, OWASP/CWE, execution/file/Git surface assessment | **No** |
| 8 | `qa-validation` | `skills/qa-validation/SKILL.md` | 67 / 3,874 B | `agents/openai.yaml` | Public-interface QA matrix (`PASS`/`FAIL`/`BLOCKED`/`NOT-TESTED`) across functional/failure/state buckets | **No** |
| 9 | `validate-runtime` | `skills/validate-runtime/SKILL.md` | 73 / 4,780 B | `agents/openai.yaml` | Isolated HOME/config, layered reachability/auth/inference/session live runtime evidence | **No** |
| 10 | `debug-ci` | `skills/debug-ci/SKILL.md` | 52 / 2,935 B | `agents/openai.yaml` | Minimal CI failure diagnosis and `docs/ci/README.md` incident knowledge updates | **No** |
| 11 | `company-llm-delegation` | `skills/company-llm-delegation/SKILL.md` | 274 / 16,671 B | `agents/openai.yaml` | Mac + company VPN bounded development-time triage routing and data boundaries | **No** |
| 12 | `start-project` | `skills/start-project/SKILL.md` | 31 / 1,348 B | `agents/openai.yaml` | Minimal testable bootstrap for new repositories/tools | **No** |

---

### 2. Antigravity CLI (`agy 1.4.0`) Skills & Customizations Verification

Comparison between official documentation (`https://antigravity.google/docs/skills/`, `https://antigravity.google/docs/rules/`, `https://antigravity.google/docs/cli/reference/`, and builtin `agy-customizations`) and local verification on `agy 1.4.0`:

| Capability | Official Docs & Builtin Spec (`DOC_VERIFIED`) | Local `agy 1.4.0` Verification (`LOCAL_VERIFIED`) |
|---|---|---|
| **Workspace Skills** | Supported. Default discovery root is `<repo>/.agents/skills/<name>/SKILL.md` (with backward compatibility for `.agent/skills/`, `_agents/skills/`, `_agent/skills/`). Each discovered skill is also registered as a `/<skill-name>` slash command in the TUI. | Neither `.agents/` nor `.agent/` exists in `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`. Workspace skills discovered: `0`. |
| **Global Skills** | Supported under `~/.gemini/antigravity-cli/skills/<name>/SKILL.md` and `~/.gemini/config/skills/<name>/SKILL.md`. | Neither directory exists on this Mac (`No such file or directory`). `agy plugin list --json` returns `[]`. |
| **Top-level `skills/` Auto-Discovery** | Top-level `skills/` is **not** scanned by default unless registered in `.agents/skills.json` (`{"entries": [{"path": "skills"}]}`). | Confirmed in current session `<skills>` prompt block: **0 of 12** repository `skills/*` are loaded. |
| **Currently Loaded Skills in `agy 1.4.0`** | Built-in skills are mounted by name from `~/.gemini/antigravity-cli/builtin/skills/`. | Only **2 built-in skills** are mounted in the active CLI session:<br>1. `agy-customizations`<br>2. `antigravity-guide` |
| **`AGENTS.md` vs `CLAUDE.md` Precedence** | `AGENTS.md` and `GEMINI.md` are hierarchical `always_on` rules (24 KB per-file cap, 20,000-token aggregate budget). Precedence: `Workspace (.agents/, AGENTS.md)` > `Declared Configs (skills.json)` > `Global (~/.gemini/config/)` > `Built-in`. | `AGENTS.md` is automatically loaded into `<user_rules>`. `CLAUDE.md` is not automatically loaded. |
| **Inspecting Loaded Skills (`/skills`)** | Interactive TUI `/skills` opens the Skills Browser overlay for local and global skills; headless `-p` mode also expands skills unless `--disable-slash-commands` is passed. | Verified via `agy --help` and `/docs/cli/reference/`. |
| **Zero-Copy Reuse of Existing `skills/`** | `.agents/skills.json` supports workspace-relative `entries` (`{"entries": [{"path": "skills"}]}`) that scan `skills/` one level deep without copying or moving files. | All 12 existing `skills/<name>/SKILL.md` files have `name` and `description` YAML frontmatter; actual fresh-session discovery after adding `.agents/skills.json` remains `NOT_TESTED` in this read-only audit. |
| **Skill vs Rule vs Subagent vs Hook vs Plugin** | - **Rule (`AGENTS.md`, `.agents/rules/*.md`)**: Always-on or trigger-scoped constraints.<br>- **Skill (`SKILL.md`)**: Progressive-disclosure workflow (only `name`+`description` loaded upfront; full text read on demand).<br>- **Subagent (`invoke_subagent`)**: Separate conversation context & toolset (`research` read-only vs `self`).<br>- **Hook (`hooks.json`)**: Synchronous command at `SessionStart`/`PreToolUse`/`PostToolUse`/`Stop`.<br>- **Plugin (`plugin.json`)**: Distributable bundle of skills, rules, hooks, and MCP configs. | Verified against builtin `agy-customizations/docs/*` and `agy` CLI subcommands. |

---

### 3. Root-Cause Diagnosis of `ISSUE120-M2-AGY-LIVE-READINESS-V1` Errors

| V1 Issue | What V1 Wrote (`#issuecomment-6220859823`) | Actual Source / Runtime Truth | Root Cause Attribution (A / B / C / D) |
|---|---|---|---|
| **1. Host JSON Top-Level Schema** | Used `"schema": "llmtool.review-host.v1"` and top-level `"capability_id"`, `"authorizer_ref"`. | `_decode_host()` (`review_authority.py:152-194`) requires exact top-level keys `('schema_version', 'agents', 'reviewer', 'route_policies', 'ledger', 'issuer')` with `"schema_version": 1` and `"issuer": {"capability_id": ..., "authorizer_ref": ...}`. | **Primary D** (`sed -n '60,145p;320,450p'` skipped lines `146–319` where `_decode_host` lives; inferred JSON keys from `HostRegistration` dataclass fields) + **Secondary B** (no mandatory step to run `_decode_host()` on report JSON snippets). |
| **2. Reviewer Role Constant** | Used `"claude_reviewer_job"` in `reviewer.role`, `route_policies[].reviewer_role`, and `worker_runtime_pin`. | `REVIEWER_ROLES = ('llmtool-review/claude-cli', 'llmtool-worker/claude-cli')` (`task_review.py:31`, `WORKFLOW.md:120`). Worker role is `"llmtool-worker/claude-cli"`. | **Primary D** (trusted an informal label from `<CONTEXT_SUMMARY>` after context compaction without re-grepping `REVIEWER_ROLES`) + **Secondary C** (`WORKFLOW.md:120` already documented `llmtool-worker/claude-cli`). |
| **3. Route Policy Entry Wrapping** | Placed `"revoked": false` flat inside the `ProviderRoutePolicy` dict. | `_decode_host()` (`review_authority.py:174-188`) requires `_keys(entry, ('policy', 'revoked'))`, i.e., `{"policy": { ...ProviderRoutePolicy... }, "revoked": false}`. | **Primary D** (skipped lines `174–188` of `review_authority.py`) + **Secondary B** (did not execute `_decode_host()` on the proposed JSON). |
| **4. `@` in `reviewer.executable` Path (New Discovery via Parser Check)** | Recommended passing `/Users/davidsun/.nvm/versions/node/v24.20.0/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe` directly in `reviewer.executable`. | `_decode_host()` calls `_path(reviewer['executable'], absolute=True)` (`action_contract.py:89`), which rejects `@` in `@anthropic-ai` (`c.isalnum() or c in '._-'`)! Meanwhile `bin/claude` is a symlink rejected by `O_NOFOLLOW` + `S_ISREG`. | **Discovered by running `_decode_host()` + `verify_worker_executable()`**: requires a conditional provisioning step (e.g., an `@`-free regular file copy or hardlink, or a separately tracked narrow path-normalization change). |
| **5. GitHub CI Run ID** | Cited `Run 24530339970` (`71705652242` / `71705652239`) for `9fa1aaba`. | `gh run list --branch main` shows the actual `databaseId` for `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39` is `38931195385`. | **Primary D/C** (after context compaction dropped the numeric ID, the model fabricated plausible-looking IDs instead of checking preserved SHA-pinned evidence or querying `gh run list`). |
| **6. Model Provenance** | Reported `Claude Sonnet 5.5 (Medium)` purely from the user prompt header. | Session crossed model switches (`Gemini 4 Argon` -> `Claude Sonnet 5.5` -> `Gemini 4 Argon`); runtime does not expose live model ID to tools. | **Primary B/D** (missing convention to distinguish `USER_PROMPT_DECLARED` from `MODEL_ID_NOT_VERIFIED`). |

---

### 4. Comparison of Options A, B, and C

| Dimension | Option A: Direct Reuse Only<br>(`.agents/skills.json` -> `skills/`) | **Option B (Recommended): Shared Skills Mount + Executable Artifact Verification**<br>(`.agents/skills.json` + shared `artifact-verification` skill) | Option C: Full Standalone `agy` Skills Suite<br>(Duplicate/rewrite skills under `.agents/skills/`) |
|---|---|---|---|
| **Maintenance Cost** | Very Low (1 file, 5 lines) | **Low** (1 `.agents/skills.json` + 1 concise shared skill; zero duplication of existing 12 skills) | High (continuous drift between `skills/` and `.agents/skills/`) |
| **Rule Duplication** | 0% | **0%** (preserves single source of truth in `skills/`) | High (violates `CLAUDE.md:90` & `ai-team-delivery`) |
| **Startup Token Cost (`UNMEASURED_ESTIMATE`)** | Unmeasured (~400 tokens estimated from frontmatter size; no token-savings claim) | **Unmeasured (~450 tokens estimated from frontmatter size; no token-savings claim)** | Higher if duplicated in `always_on` rules |
| **Effect on V1-Class Errors** | Partial risk reduction (exposes `inspect-repo`, but lacks explicit parser-check step for report artifacts) | **Risk reduction (not a guarantee)**: prompts agents to run existing parsers (`_decode_host`, `llmtool review-authority check`) on concrete fixtures; skills never substitute for actual parser execution or CI | Low-Medium (prose rules without parser execution do not prevent schema/ID errors) |
| **Impact on Codex & Claude** | None | **Positive** (shared `artifact-verification` checklist is reusable by Codex, Claude, and `agy`; follow-up improvement, not an M2 blocker) | Negative (splits team workflow rules) |

---

### 5. Proposed Minimal Structure (Design Only — Not Created in Repo, Not an M2 Prerequisite)

1. **`.agents/skills.json`** (enables `agy` to discover shared `skills/*` in-place; `TEMPLATE_ONLY`):
   ```json
   {
     "entries": [
       { "path": "skills" }
     ]
   }
   ```
2. **`skills/artifact-verification/SKILL.md`** (shared follow-up checklist across Codex, Claude, and `agy` to reduce—though never guarantee against—documentation/runbook drift):
   - **Rule 1 (No Unread Truncation / No Summary-Only Constants):** Never infer schemas from truncated output or `<CONTEXT_SUMMARY>`; read the exact parser function and grep constant definitions (`REVIEWER_ROLES`).
   - **Rule 2 (Execute Real Parsers on Concrete Synthetic Fixtures):** Distinguish human-readable `TEMPLATE_ONLY` blocks (with placeholders that fail parsing) from concrete `SYNTHETIC_PARSER_VERIFIED` fixtures tested through `_decode_host`, `read_host_file`, `llmtool review-authority check --host ... --json`, or `parse_task_contract`.
   - **Rule 3 (Preserve Pinned Evidence & Verify Unrecorded External IDs):** Reuse timestamped, SHA-pinned prior evidence without rerunning unchanged tests or queries; when a specific external identifier (e.g., GitHub Run ID, comment URL, `st_dev`/`st_ino`, SHA256) was not preserved in the record, query it once or mark it `NOT_QUERIED`.
   - **Rule 4 (Evidence & Model Provenance Separation):** Keep `TEMPLATE_ONLY`, `SYNTHETIC_PARSER_VERIFIED`, `SOURCE_VERIFIED`, `FAKE_TEST_VERIFIED`, `HELP_VERIFIED`, `USER_VERIFIED`, `LIVE_NOT_VERIFIED / NOT_AUTHORIZED`, and `INDEPENDENT_REVIEW_PASS` separate; report `USER_PROMPT_DECLARED` vs `MODEL_ID_NOT_VERIFIED` when runtime model identity is unavailable.

---

## Part II — `ISSUE120-M2-AGY-LIVE-READINESS-V2` (Correction of V1 Schema, Role & Evidence Labels)

This section supersedes the erroneous schema, role, and Run ID values in V1 (`#issuecomment-6220859823`). It strictly separates:
- **`TEMPLATE_ONLY`**: Human-readable runbook/JSON templates containing `<...>` placeholders or unprovisioned paths (which fail `_decode_host()` if passed literally).
- **`SYNTHETIC_PARSER_VERIFIED`**: Concrete disposable synthetic fixtures tested offline against `llmtool.review_authority._decode_host()`, `read_host_file()`, `verify_worker_executable()`, and `llmtool review-authority check --host <synthetic-fixture> --json` on `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39`.
- **`LIVE_NOT_VERIFIED / NOT_AUTHORIZED`**: Production Host file installation, `PROVIDER_REVIEW` SQLite Ledger creation, dedicated `reviewer_auth_home` provisioning/login, Claude CLI worker execution, and live model inference.

### 1. Corrected Gate & CI Table (`9fa1aaba93b8cfe1d95179a392b2ad33bba54b39`)

| Item | Status | Verified Evidence |
|---|---|---|
| `origin/main` HEAD | `READY` (`SOURCE_VERIFIED`) | `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39` (PR #165 at `86a990eb` + PR #166 WJ1 fix at `9fa1aaba`) |
| Post-merge `main` push CI on `9fa1aaba` | `READY` (`SUCCESS` on `main`) | `gh run list --branch main`: Workflow `llmtool CI`, `push` Run `databaseId` **`38931195385`** on `main` `9fa1aaba93b8cfe1d95179a392b2ad33bba54b39` (`createdAt: 2026-10-16T16:20:39Z`, Python 3.9 & 3.14 jobs `SUCCESS`). Note: this is `main` push CI, **not** PR #169 exact-head CI (the workflow path filter excludes `reports/*`-only diffs). |
| `review_authority_factory()` (`review_authority.py:568-570`) | `BLOCKED` (by design) | Returns `UnavailableReviewAuthority()`; public CLI `llmtool task review` returns `REVIEW_AUTHORITY_UNAVAILABLE` |
| Original Claude (`081de22e-d514-4288-9d1f-48f488979271`) WJ1 Delta Review | `BLOCKED` | Paused due to quota until 2026-10-18; PR #166 in-session Reviewer Subagent (`#issuecomment-6219655712`) is author-side pre-review only |
| Production Host, Ledger, Auth HOME, CLI & Live Model Trial | `LIVE_NOT_VERIFIED / NOT_AUTHORIZED` | No production Host file, SQLite ledger, or dedicated auth HOME is installed; no live Claude CLI worker or model call is authorized or executed |

### 2. Critical Path-Vocabulary Finding (`WJ5` + `action_contract._path`) & Conditional Provisioning Options

- **Why the raw npm path fails `_decode_host()` (`SYNTHETIC_PARSER_VERIFIED`):**
  - `/Users/davidsun/.nvm/versions/node/v24.20.0/bin/claude` is a symlink (`lrwxr-xr-x`), which is rejected by `load_host_registration()` (`review_authority.py:245-246`) and `verify_worker_executable()` (`review_authority.py:328-330`, `O_NOFOLLOW` + `stat.S_ISREG`).
  - Its resolved target `/Users/davidsun/.nvm/versions/node/v24.20.0/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe` is a physical regular file (`260,406,992` bytes, SHA256 `3439427bfce962fddcf557c5a058ca914fa63e06fee894f8e12a3f10e04d1871`, `st_dev=16777223`), **but** its path component `@anthropic-ai` contains `@`, which is rejected by `_path()` (`action_contract.py:89`: `any(not (c.isalnum() or c in '._-') for c in part)`).
- **Conditional Provisioning Options (`TEMPLATE_ONLY / provisioning NOT_AUTHORIZED` — do not execute until authorized):**
  1. **Option 1 — Hardlink in an `@`-free `0700` directory (`CONDITIONAL_WORKAROUND`, `SYNTHETIC_PARSER_VERIFIED`, `LIVE_NOT_VERIFIED`):**
     ```sh
     # TEMPLATE_ONLY / provisioning NOT_AUTHORIZED (do not run without Integrator/Owner authorization):
     install -d -m 0700 /Users/davidsun/.local/share/llmtool-host-authority/bin
     ln /Users/davidsun/.nvm/versions/node/v24.20.0/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe \
        /Users/davidsun/.local/share/llmtool-host-authority/bin/claude-2.1.301
     ```
     - **Operational & upgrade caveats:** `verify_worker_executable()` allows regular multi-link files and checks the 256-MiB cap plus exact SHA256 (`3439427bfce962fddcf557c5a058ca914fa63e06fee894f8e12a3f10e04d1871`). In-place writes to an aliased inode are rejected by SHA256 verification. However, if `npm` upgrades Claude Code via atomic rename/replacement, the hardlink will remain attached to the **old inode/version (`2.1.301`)** and continue matching the old pin—there is **no automatic upgrade tracking or revocation**. Adopting any updated binary requires explicit version, SHA256, `worker_runtime_digest`, and `HostPin` re-verification under applicable authorization.
  2. **Option 2 — Authorized private regular-file copy (`CONDITIONAL_WORKAROUND`):** Copying the verified binary to `/Users/davidsun/.local/share/llmtool-host-authority/bin/claude-2.1.301` (`mode 0755`) avoids inode aliasing with `npm`, at the cost of ~248.34 MiB disk space and the same manual re-pinning requirement on upgrade.
  3. **Option 3 — Future narrow source change (`LATER / NEXT`, nonblocking):** Track a narrowly scoped executable-path normalization change separately if direct npm `@scope` paths should be supported without relaxing the general `action_contract._path()` validator.

### 3. Runtime Pin, `TEMPLATE_ONLY` Host Registration Pattern & `SYNTHETIC_PARSER_VERIFIED` Fixture Evidence

- **Configuration values bound into `worker_runtime_digest` (`SYNTHETIC_PARSER_VERIFIED`; does not establish installed runtime readiness):**
  - `role`: `"llmtool-worker/claude-cli"` (`REVIEWER_ROLES[1]` in `task_review.py:31`)
  - `executable`: `"/Users/davidsun/.local/share/llmtool-host-authority/bin/claude-2.1.301"`
  - `version`: `"2.1.301"` (static string pin; `verify_worker_executable` does not execute `--version`)
  - `executable_sha256`: `"3439427bfce962fddcf557c5a058ca914fa63e06fee894f8e12a3f10e04d1871"`
  - `auth_home`: `"/Users/davidsun/.local/share/llmtool-reviewer-auth-home"` (`LIVE_NOT_VERIFIED / NOT_AUTHORIZED`)
  - **Computed `worker_runtime_digest` (`llmtool.worker-runtime-pin.v1`):** `aee9d198f75905f14fd609bcfd65317b07a02febf58aeb36d7a82c30404559fb`

#### 3.1 Parameterized Host Registration Pattern (`TEMPLATE_ONLY` — Rejected by Parser if Run Unsubstituted)

> **Warning (`TEMPLATE_ONLY`):** The block below contains `<...>` placeholders (`<AUTHORIZING_COMMENT_ID>`, `<AUTHORIZED_CLAUDE_MODEL_ID>`, `<UTC_ISO8601_START>`, `<UTC_ISO8601_END>`, `<INT st_dev...>`, `<INT st_ino...>`, `<64-HEX creation_nonce...>`). Passing this literal template to `llmtool review-authority check --host <file> --json` fails closed with **exit code `2`**, `code: "REVIEW_HOST_POLICY_INVALID"`, and `structural_validation: "INVALID"`. Production Host/ledger/auth-HOME provisioning remains **`LIVE_NOT_VERIFIED / NOT_AUTHORIZED`**.

```json
{
  "schema_version": 1,
  "issuer": {
    "capability_id": "HOST_CAP_M2_TRIAL_V1",
    "authorizer_ref": "https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-<AUTHORIZING_COMMENT_ID>"
  },
  "agents": [
    {
      "agent_id": "codex",
      "root": "/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex",
      "device": 16777223,
      "inode": 145350019,
      "common_dir": "/Users/davidsun/git/davidcataiporject/davidcattoolsproject/.git",
      "origin": "git@github.com:davidcatcat/davidcattoolsproject.git",
      "branch_prefix": "feat/codex-",
      "venv_prefix": "/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex/tools/llmtool/.venv"
    },
    {
      "agent_id": "claude",
      "root": "/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude",
      "device": 16777223,
      "inode": 145350626,
      "common_dir": "/Users/davidsun/git/davidcataiporject/davidcattoolsproject/.git",
      "origin": "git@github.com:davidcatcat/davidcattoolsproject.git",
      "branch_prefix": "feat/claude-",
      "venv_prefix": "/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude/tools/llmtool/.venv"
    }
  ],
  "reviewer": {
    "role": "llmtool-worker/claude-cli",
    "executable": "/Users/davidsun/.local/share/llmtool-host-authority/bin/claude-2.1.301",
    "version": "2.1.301",
    "executable_sha256": "3439427bfce962fddcf557c5a058ca914fa63e06fee894f8e12a3f10e04d1871",
    "auth_home": "/Users/davidsun/.local/share/llmtool-reviewer-auth-home"
  },
  "route_policies": [
    {
      "policy": {
        "policy_id": "M2_CLAUDE_REVIEWER_JOB_TRIAL_V1",
        "version": 1,
        "provider": "claude-cli",
        "profile": "reviewer_worker_trial",
        "model": "<AUTHORIZED_CLAUDE_MODEL_ID>",
        "data_classes": ["DIFF", "REVIEW_METADATA", "SOURCE"],
        "max_packet_bytes": 65536,
        "call_timeout_seconds": 90,
        "run_timeout_seconds": 120,
        "max_attempts": 1,
        "retry_count": 0,
        "fallback_count": 0,
        "per_call_cap": 1,
        "per_day_cap": 1,
        "unknown_cost_accepted": true,
        "not_before": "<UTC_ISO8601_START>",
        "not_after": "<UTC_ISO8601_END>",
        "authorizer_ref": "https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-<AUTHORIZING_COMMENT_ID>",
        "reviewer_role": "llmtool-worker/claude-cli",
        "worker_runtime_digest": "aee9d198f75905f14fd609bcfd65317b07a02febf58aeb36d7a82c30404559fb"
      },
      "revoked": false
    }
  ],
  "ledger": {
    "path": "/Users/davidsun/.local/share/llmtool-host-authority/provider-review-ledger.sqlite3",
    "device": "<INT st_dev from created ledger>",
    "inode": "<INT st_ino from created ledger>",
    "creation_nonce": "<64-HEX creation_nonce from ReceiptLedger.create(..., kind='PROVIDER_REVIEW')>"
  }
}
```

#### 3.2 Concrete Synthetic Fixture & Structural CLI Check (`SYNTHETIC_PARSER_VERIFIED` — Grants No Live Authority)

- **Synthetic fixture substitutions used for offline parser verification (grants nothing; not a live authorization):**
  - `issuer.authorizer_ref` & `route_policies[0].policy.authorizer_ref`: `"https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6220859823"` (syntactically valid comment URL used as a dummy test value only; supplies no grant).
  - `route_policies[0].policy.model`: `"claude-sonnet-4-5-20250929"`
  - `route_policies[0].policy.not_before`: `"2026-10-16T00:00:00Z"`
  - `route_policies[0].policy.not_after`: `"2026-10-20T00:00:00Z"`
  - `ledger.device`: `16777223` (synthetic integer)
  - `ledger.inode`: `12345678` (synthetic integer)
  - `ledger.creation_nonce`: `"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"` (synthetic 64-hex string)
- **Concrete synthetic fixture digests & commands:**
  - **Author disposable synthetic fixture (`json.dump(..., indent=2)`, mode `0600` in a `0700` `/private/tmp` directory):**
    - Fixture SHA256 (`host_digest`): `67ff5302b3c84d4339bd1f8de9b0c05881b013c868704b259e02fd87103a1b18`
    - Command: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/llmtool review-authority check --host <disposable-synthetic-fixture> --json`
    - Exit code: `0`
    - Output:
      ```json
      {"authorized":false,"code":"REVIEW_HOST_STRUCTURE_VALID","collector_status":"NOT_ATTESTED","cost":null,"host_digest":"67ff5302b3c84d4339bd1f8de9b0c05881b013c868704b259e02fd87103a1b18","input_tokens":null,"invocation_status":"NOT_CALLED","output_tokens":null,"production_handoff":"NOT_DELIVERED","provider_calls":0,"published_pin_validation":"NOT_VERIFIED","reported_state":null,"runtime_evidence_status":"NOT_VERIFIED","schema_version":1,"status":"BLOCKED","structural_validation":"SUCCESS","test_only":false,"verdict_validation":"NOT_VERIFIED"}
      ```
  - **Original Codex Integrator independent verification (`#issuecomment-6221624501`, preserved without re-running):**
    - Command: `tools/llmtool/.venv/bin/python -B -I /private/tmp/issue120-pr169-independent-jzq7dx15/verify.py` -> exit `0` (18/18 focused parser/path/digest cases; private results `/private/tmp/issue120-pr169-independent-jzq7dx15/results.json`, SHA256 `cebacc4b5c3806df8a726e6e6a2bcb4ab9c99722b1ea7a999046efd2e791734a`).
    - CLI check on literal `TEMPLATE_ONLY` block -> exit `2`, `code: "REVIEW_HOST_POLICY_INVALID"`, `structural_validation: "INVALID"`.
    - CLI check on Integrator's fully substituted synthetic fixture (SHA256 `8088b7da13f72dce908f2e64b06dd40a5bdc27d1183afeb0df7dccd8205889ed`) -> exit `0`, `code: "REVIEW_HOST_STRUCTURE_VALID"`, `status: "BLOCKED"`, `authorized: false`, `provider_calls: 0`, `published_pin_validation: "NOT_VERIFIED"`, `runtime_evidence_status: "NOT_VERIFIED"`.
- **Verification Scope & Limitations:**
  - `llmtool review-authority check` (`read_host_file`) validates static JSON structure, path vocabulary (`_path`), parent `0700` physical directory, file `0600` regular/single-link ownership, and repository-root exclusion only.
  - It does **not** open or verify the SQLite ledger (`load_host_registration` is not called), does **not** verify `reviewer_auth_home` or `reviewer_executable` on disk, executes no binary or model, and grants **no** live execution authority (`authorized: false`, `status: "BLOCKED"`).
