# Standard Dispatch Specification Template Design (#1817)

## 1. Motivation and Problem Analysis

Most failed agent dispatch runs in the past originated from manually authored or malformed PM dispatch specifications:
- Missing writable files in `write_paths`, causing immediate scope refusals during implementation.
- Wrong base commits or stale local branches, leading to source staleness or merge divergence.
- Missing predecessor specs, broken continuation lineage, or lost carried counts across repairs and re-reviews.
- Inconsistent link syntax, non-relative file links, or illegal line anchors in documentation and criteria.

Owner guidance establishes the core requirement:
> 「範本要涵蓋你派給所有 AI 角色的工作：Developer、唯讀調查、AI Debug、Reviewer、子 PM，每種一個固定格式，讓你只要照填、不用猜。範本要能讓工具自動帶欄位、檢查漏洞，不只是一份文件。」
>
> 「PM 沒有照標準規格寫的工作，一律退回 PM 重寫，不准派出去執行。最好由工具在派工前自動檢查、自動退回；派出去後才被 Developer 發現的，記成 PM 規格錯誤。」

To fulfill this mandate, dispatch specifications must move from ad-hoc JSON payloads to strictly structured, role-specific templates. Field assignment is strictly bifurcated:
- **PM fills human intent only**: problem statement / context, bounded goal, and concrete acceptance criteria.
- **Tooling automatically populates operational fields**: base commit SHA, calculated read/write scopes, predecessor references, continuation lineage, carried repair/failure counts, author/worker bindings, and preflight check receipts.

Any dispatch spec failing automated pre-dispatch checks is refused immediately before dispatch.

---

## 2. Fixed Field Sets Across the Five Dispatch Kinds

Five distinct dispatch kinds cover all AI roles in the team:
1. `developer`: Code or documentation implementation, feature development, or repair tasks.
2. `investigation`: Read-only analysis, architectural inspection, and feasibility exploration (PM Mode A).
3. `debug`: Read-only root-cause diagnosis and reproduction via High Debug (`codex-debug` / `claude-debug`).
4. `reviewer`: Read-only formal cross-family code review (`claude-reviewer` / `codex-reviewer`).
5. `sub_pm`: Sub-agent PM execution (PM Mode B) managing a bounded single work package lease.

### 2.1 Field Responsibility Matrix

| Field Name | Description | Source / Populated By | Kinds Applicable |
|---|---|---|---|
| `kind` | Type of dispatch (`developer`, `investigation`, `debug`, `reviewer`, `sub_pm`) | PM selection / Tool template | All |
| `problem` | User request, incident context, or failure symptom | **PM** | All |
| `goal` | Terse summary of objective (&le; 512 UTF-8 bytes) | **PM** | All |
| `acceptance` | List of 1&ndash;16 verifiable criteria (&le; 512 bytes each) | **PM** | All |
| `task_id` | Registered Task UUID | **Tool** (TaskStore) | All |
| `run_id` | Unique execution run UUID | **Tool** (Runner) | All |
| `base` | Exact 40-character commit SHA verified against current repository HEAD | **Tool** (Git / TaskStore) | All |
| `read_paths` | Allowlist of readable repository paths | **Tool** (computed from acceptance, tests, and context) | All |
| `write_paths` | Allowlist of writable repository paths | **Tool** (strictly empty for read-only roles; validated for developer/sub-PM) | All |
| `predecessor_spec` | Prior task ID and contract digest in this lineage | **Tool** (lineage tracker, required on repair/rereview) | All |
| `continued_lineage` | Ordered list of ancestor task/run IDs | **Tool** (lineage tracker) | All |
| `carried_counts` | Mapping of carried repairs, test failures, and attempts | **Tool** (audit counter) | All |
| `author` | Originating PM actor and worker assignment route | **Tool** (session / dispatch gate) | All |
| `check_results` | Preflight check receipt and validation status | **Tool** (preflight validator) | All |
| `role_card_digest` | SHA-256 digest of main-pinned role card | **Tool** (harness controller) | All |
| `contract_digest` | SHA-256 digest of bounded task contract | **Tool** (contract generator) | All |

---

### 2.2 Role-Specific Template Specifications

#### 1. Developer Spec (`kind: "developer"`)
- **Write Scope**: Must contain at least one repository-relative path in `write_paths`.
- **Read Scope**: Must include every path in `write_paths`, all paths cited in `acceptance`, and at least one admitted test module under `tools/llmtool/tests/` (or `scripts/` if writing solely under `scripts/`) when code files are modified.
- **Constraints**: No `edits` field allowed.

```json
{
  "kind": "developer",
  "task_id": "3b29c91f4a5649988ffbc66258aa4881",
  "run_id": "a901844b2f154cc98d1a6ec71037f002",
  "base": "611d4cb31d35c1a1becf7b197d1ee08e86968268",
  "author": {
    "role": "pm",
    "worker": "claude-developer",
    "route": "ALLOWED"
  },
  "problem": "Implement standard dispatch spec template parser and pre-dispatch checks.",
  "goal": "Add dispatch spec template parser and preflight validation script.",
  "acceptance": [
    "A new module tools/llmtool/llmtool/spec_template.py defines templates for 5 dispatch kinds.",
    "tools/llmtool/scripts/spec_preflight.py validates template fields and returns refusal codes.",
    "tools/llmtool/tests/test_spec_preflight.py covers all refusal paths."
  ],
  "read_paths": [
    "CLAUDE.md",
    "tools/llmtool/WORKFLOW.md",
    "tools/llmtool/scripts/spec_preflight.py",
    "tools/llmtool/tests/test_spec_preflight.py",
    "tools/llmtool/llmtool/spec_template.py"
  ],
  "write_paths": [
    "tools/llmtool/llmtool/spec_template.py",
    "tools/llmtool/scripts/spec_preflight.py"
  ],
  "predecessor_spec": null,
  "continued_lineage": [],
  "carried_counts": {
    "repairs": 0,
    "test_failures": 0,
    "attempts": 1
  },
  "check_results": {
    "preflight_status": "SPEC_PREFLIGHT_OK",
    "d6_worktree_registered": true,
    "source_synced": true
  },
  "role_card_digest": "38a7c0...",
  "contract_digest": "49bf21..."
}
```

#### 2. Read-Only Investigation Spec (`kind: "investigation"`)
- **Write Scope**: Strictly empty `[]`.
- **Read Scope**: Target inspection directories and reference documents.
- **Goal / Acceptance**: Read-only exploratory findings, architectural options, or feasibility analysis.

```json
{
  "kind": "investigation",
  "task_id": "c1a938e55e8140f28e21051515286121",
  "run_id": "119e7a8374a54a84976725ea35fa016b",
  "base": "611d4cb31d35c1a1becf7b197d1ee08e86968268",
  "author": {
    "role": "pm",
    "worker": "claude-subpm",
    "route": "ALLOWED"
  },
  "problem": "Evaluate feasibility of caching git status queries during test execution.",
  "goal": "Inspect runner lifecycle scripts and document cache invalidation risks.",
  "acceptance": [
    "Report lists all call sites of git status in tools/llmtool/scripts/.",
    "Risk assessment identifies any mutation points that bypass invalidation hooks."
  ],
  "read_paths": [
    "tools/llmtool/WORKFLOW.md",
    "tools/llmtool/scripts/d6_worktree_lifecycle.py"
  ],
  "write_paths": [],
  "predecessor_spec": null,
  "continued_lineage": [],
  "carried_counts": {
    "repairs": 0,
    "test_failures": 0,
    "attempts": 1
  },
  "check_results": {
    "preflight_status": "SPEC_PREFLIGHT_OK",
    "d6_worktree_registered": true,
    "source_synced": true
  },
  "role_card_digest": "89f30b...",
  "contract_digest": "1172a1..."
}
```

#### 3. AI Debug Spec (`kind: "debug"`)
- **Write Scope**: Strictly empty `[]` (`HIGH_DEBUG_WROTE_FILES` guard).
- **Read Scope**: Source files under investigation, failing reproducer, test module, and error logs.
- **Lineage**: Mandatory `predecessor_spec` and `continued_lineage` referencing the failing execution run.
- **Reporting**: Mandates mandatory sections defined in [high-debug-report.md](../../.claude/skills/ai-team-delivery/high-debug-report.md).

```json
{
  "kind": "debug",
  "task_id": "78a9c211516e4ab4a719992c68fa8115",
  "run_id": "456811239aa841cbb19280d0d885a004",
  "base": "611d4cb31d35c1a1becf7b197d1ee08e86968268",
  "author": {
    "role": "pm",
    "worker": "codex-debug",
    "route": "ALLOWED"
  },
  "problem": "Second review FAIL on Task 3b29c91f due to bracket parsing in spec preflight.",
  "goal": "Diagnose root cause of regex mismatch in _ends_inside_open_bracket.",
  "acceptance": [
    "High Debug report identifies exact failure mechanism in spec_preflight.py.",
    "Report specifies repo-relative files and lines required for repair."
  ],
  "read_paths": [
    "tools/llmtool/scripts/spec_preflight.py",
    "tools/llmtool/tests/test_spec_preflight.py"
  ],
  "write_paths": [],
  "predecessor_spec": {
    "task_id": "3b29c91f4a5649988ffbc66258aa4881",
    "contract_digest": "49bf21..."
  },
  "continued_lineage": [
    "3b29c91f4a5649988ffbc66258aa4881"
  ],
  "carried_counts": {
    "repairs": 1,
    "test_failures": 0,
    "attempts": 2
  },
  "check_results": {
    "preflight_status": "SPEC_PREFLIGHT_OK",
    "d6_worktree_registered": true,
    "source_synced": true
  },
  "role_card_digest": "d7481a...",
  "contract_digest": "aa8912..."
}
```

#### 4. Reviewer Spec (`kind: "reviewer"`)
- **Write Scope**: Strictly empty `[]`.
- **Read Scope**: Task contract, full diff, test files, CI logs, and runtime evidence.
- **Model Pairing**: Verified cross-family route (Codex Reviewer for Claude work, Claude Reviewer for AGY/OpenCode/gw-agent work) per [formal-verdict.md](../../.claude/skills/ai-team-delivery/formal-verdict.md).

```json
{
  "kind": "reviewer",
  "task_id": "01bcf9128aa14175a18290fa8192ca18",
  "run_id": "59218ab28e4444589d71fa99018442a0",
  "base": "611d4cb31d35c1a1becf7b197d1ee08e86968268",
  "author": {
    "role": "pm",
    "worker": "codex-reviewer",
    "route": "ALLOWED"
  },
  "problem": "Formal cross-family review of PR 2331 head commit.",
  "goal": "Review spec template implementation against PR acceptance and test gates.",
  "acceptance": [
    "Formal verdict comment posted with review_verdict marker and exact HEAD binding.",
    "All findings classified with F<n> [Severity] format."
  ],
  "read_paths": [
    "tools/llmtool/llmtool/spec_template.py",
    "tools/llmtool/scripts/spec_preflight.py",
    "tools/llmtool/tests/test_spec_preflight.py"
  ],
  "write_paths": [],
  "predecessor_spec": {
    "task_id": "3b29c91f4a5649988ffbc66258aa4881",
    "contract_digest": "49bf21..."
  },
  "continued_lineage": [
    "3b29c91f4a5649988ffbc66258aa4881"
  ],
  "carried_counts": {
    "repairs": 0,
    "test_failures": 0,
    "attempts": 1
  },
  "check_results": {
    "preflight_status": "SPEC_PREFLIGHT_OK",
    "d6_worktree_registered": true,
    "source_synced": true
  },
  "role_card_digest": "bb107e...",
  "contract_digest": "90cf12..."
}
```

#### 5. Sub-PM Spec (`kind: "sub_pm"`)
- **Write Scope**: Bounded work package directory paths granted under the `subpm-lease`.
- **Read Scope**: Full work package context, architecture notes, and task lists.
- **Constraints**: Lease registration verified in issue comments before dispatch per [pm-continuation.md](../../.claude/skills/ai-team-delivery/pm-continuation.md).

```json
{
  "kind": "sub_pm",
  "task_id": "99ea78b104924a189fbbca718290f112",
  "run_id": "77189fa1891244e8bc1928001824ca88",
  "base": "611d4cb31d35c1a1becf7b197d1ee08e86968268",
  "author": {
    "role": "pm",
    "worker": "claude-subpm",
    "route": "ALLOWED"
  },
  "problem": "Coordinate work package WP-H: sub-agent PM role card delivery.",
  "goal": "Execute work package WP-H tasks under exclusive subpm-lease.",
  "acceptance": [
    "Dispatches developer and reviewer for WP-H subtasks.",
    "Records clean hand-back on completion of work package."
  ],
  "read_paths": [
    "tools/llmtool/WORKFLOW.md",
    ".claude/skills/role-cards/pm.md"
  ],
  "write_paths": [
    ".claude/skills/role-cards/"
  ],
  "predecessor_spec": null,
  "continued_lineage": [],
  "carried_counts": {
    "repairs": 0,
    "test_failures": 0,
    "attempts": 1
  },
  "check_results": {
    "preflight_status": "SPEC_PREFLIGHT_OK",
    "d6_worktree_registered": true,
    "lease_registered": true,
    "source_synced": true
  },
  "role_card_digest": "55cc2a...",
  "contract_digest": "33fa09..."
}
```

---

## 3. Pre-Dispatch Automated Validation and Refusal Codes

Tools must automatically validate every candidate dispatch specification prior to executing dispatch commands (`llmtool task delegate`, `llmtool task repair`, etc.).

### 3.1 Refusal Enforcement Rules
- If any check fails, the tool aborts dispatch, writes no files to the worktree, emits a deterministic refusal code, and returns the specification to the PM for rewriting.
- If a specification is dispatched without passing this gate and the Developer later halts due to a specification defect (such as an unlisted write path or stale base), the failure is audited and recorded as a **PM specification error (`PM_SPEC_ERROR`)**.

### 3.2 Refusal Code Catalogue

| Refusal Code | Category | Condition / Invariant |
|---|---|---|
| `SPEC_INVALID_JSON` | Syntax | Input is not valid JSON or root is not an object. |
| `SPEC_UNKNOWN_KIND` | Schema | Field `kind` is missing or not one of the 5 allowed values. |
| `SPEC_PM_FIELD_MISSING` | Schema | Missing required PM field: `problem`, `goal`, or `acceptance`. |
| `SPEC_GOAL_TOO_LONG` | Limits | `goal` exceeds 512 UTF-8 bytes. |
| `SPEC_ACCEPTANCE_COUNT_INVALID` | Limits | `acceptance` has 0 or more than 16 items. |
| `SPEC_ACCEPTANCE_ITEM_TOO_LONG` | Limits | Any single acceptance item exceeds 512 UTF-8 bytes. |
| `SPEC_EDITS_FORBIDDEN` | Policy | Spec carries forbidden `edits` field ([spec_preflight.py](../../tools/llmtool/scripts/spec_preflight.py)). |
| `SPEC_SENSITIVE_CONTENT` | Security | Any text field contains credential-shaped strings or fails the sensitive detector screen ([pm-continuation.md](../../.claude/skills/ai-team-delivery/pm-continuation.md)). |
| `SPEC_BASE_STALE` | Git / State | Spec `base` does not match canonical `origin/main` commit ([pm-continuation.md](../../.claude/skills/ai-team-delivery/pm-continuation.md)). |
| `SPEC_SCOPE_READ_MISSING_ACCEPTANCE` | Scope | A repository path cited in `acceptance` is missing from `read_paths` ([spec_preflight.py](../../tools/llmtool/scripts/spec_preflight.py)). |
| `SPEC_SCOPE_FILE_NOT_FOUND` | Scope | A path listed in `read_paths` does not exist in the repository ([spec_preflight.py](../../tools/llmtool/scripts/spec_preflight.py)). |
| `SPEC_SCOPE_TEST_MODULE_MISSING` | Scope | Code modification under `tools/` or `scripts/` lacks an admitted test module in `read_paths` ([spec_preflight.py](../../tools/llmtool/scripts/spec_preflight.py)). |
| `SPEC_WRITE_PATHS_EMPTY` | Scope | `developer` or `sub_pm` dispatch has an empty `write_paths` array. |
| `SPEC_READONLY_ROLE_HAS_WRITES` | Scope | `investigation`, `debug`, or `reviewer` has a non-empty `write_paths` array ([debug.md](../../.claude/skills/role-cards/debug.md), [reviewer.md](../../.claude/skills/role-cards/reviewer.md)). |
| `SPEC_PREDECESSOR_MISSING` | Lineage | Repair or re-review dispatch lacks `predecessor_spec` or unbroken lineage tracking ([WORKFLOW.md](../../tools/llmtool/WORKFLOW.md)). |
| `SPEC_INVALID_LINK_SYNTAX` | Docs / Quality | Markdown links in spec criteria contain line anchors (`#L...`) instead of pure relative file paths ([WORKFLOW.md](../../tools/llmtool/WORKFLOW.md)). |
| `SPEC_DISPATCH_UNAUTHORIZED` | Authority | Target worker route is not `ALLOWED` in `worker-dispatch.json` ([CLAUDE.md](../../CLAUDE.md)). |
| `SPEC_D6_CONFLICT` | D6 Lifecycle | Assigned D6 worktree is already bound to another active writer ([developer.md](../../.claude/skills/role-cards/developer.md)). |

---

## 4. Cited Rule and Tool Foundations

Every field and pre-dispatch check in this design directly derives from established repository governance and automation:
- **Preflight validator script**: [spec_preflight.py](../../tools/llmtool/scripts/spec_preflight.py) screens text limits, byte boundaries, forbidden `edits`, acceptance item counts, and read scope coverage.
- **Preflight test suite**: [test_spec_preflight.py](../../tools/llmtool/tests/test_spec_preflight.py) tests strict JSON loading, boundary handling, and screen refusal mechanics.
- **Workflow & Delivery gates**: [WORKFLOW.md](../../tools/llmtool/WORKFLOW.md) governs the progression of tasks through remote testing, PR creation, Linux CI, and review.
- **Repository Authority**: [CLAUDE.md](../../CLAUDE.md) establishes worker dispatch rules, cross-family review requirements, and single-writer constraints.
- **PM Role Card**: [pm.md](../../.claude/skills/role-cards/pm.md) details dispatch hygiene, review handoff, and lease rules.
- **Developer Role Card**: [developer.md](../../.claude/skills/role-cards/developer.md) restricts execution strictly to assigned D6 and bounded `write_paths`.
- **Reviewer Role Card**: [reviewer.md](../../.claude/skills/role-cards/reviewer.md) defines read-only context and mandatory formal review verdict formatting.
- **Debug Role Card**: [debug.md](../../.claude/skills/role-cards/debug.md) mandates strictly read-only diagnostics and structured RCA reporting.
- **Formal Verdict Protocol**: [formal-verdict.md](../../.claude/skills/ai-team-delivery/formal-verdict.md) standardizes cross-family review findings and bindings.
- **High Debug Report Specification**: [high-debug-report.md](../../.claude/skills/ai-team-delivery/high-debug-report.md) specifies mandatory sections and diagnostic evidence.
- **PM Continuation Contract**: [pm-continuation.md](../../.claude/skills/ai-team-delivery/pm-continuation.md) governs next-decision obligations, sensitive screen handling, and sub-PM leases.
- **PM Continuation Reference**: [pm-continuation-reference.md](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md) defines repair caps, retry procedures, and quota recovery.

---

## 5. Implementation Roadmap in Small Pull Requests

Implementation of this design is phased into small, independent PRs. No production code changes are introduced in this design document task.

### PR 1: Core Template Schema & Data Builder
- **Goal**: Implement standard template definitions and tool-side automatic field population.
- **Files**:
  - `tools/llmtool/llmtool/spec_template.py` (new module defining dataclasses/schemas for the 5 kinds and auto-fill logic)
  - `tools/llmtool/tests/test_spec_template.py` (unit tests validating auto-fill of base SHA, read paths, lineage, and digests)

### PR 2: Preflight Automated Validation Expansion
- **Goal**: Integrate all refusal codes into `spec_preflight.py` and provide a standalone validation entrypoint.
- **Files**:
  - `tools/llmtool/scripts/spec_preflight.py` (add checks for kind schemas, write scope rules, link syntax, and refusal codes)
  - `tools/llmtool/llmtool/errors.py` (add error constants for standard refusal codes)
  - `tools/llmtool/tests/test_spec_preflight.py` (test coverage for all refusal paths)

### PR 3: Dispatch Enforcement Hook in Task Submission CLI
- **Goal**: Enforce automatic pre-dispatch checks inside `llmtool task delegate` and `llmtool task repair`.
- **Files**:
  - `tools/llmtool/llmtool/task_submission_cli.py` (invoke preflight before recording or dispatching tasks; reject invalid specs back to PM)
  - `tools/llmtool/tests/test_task_submission_cli.py` (integration tests asserting pre-dispatch abort and PM spec error logging)

### PR 4: Process and Documentation Alignment
- **Goal**: Update delivery skills to reference standard dispatch templates and procedures.
- **Files**:
  - `.claude/skills/ai-team-delivery/pm-continuation.md` (update dispatch hygiene section with standard spec workflow)
  - `.claude/skills/ai-team-delivery/pm-continuation-reference.md` (add troubleshooting guide for spec refusal codes)
- **Dependency Note**: Linking the PM role card (`.claude/skills/role-cards/pm.md`) to the standard template waits for PR #2330.
