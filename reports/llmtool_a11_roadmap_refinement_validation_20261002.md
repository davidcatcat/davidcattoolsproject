# PR #13 roadmap refinement validation — 2026-10-02

## Scope and revision

Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Existing PR #13 branch: `docs/a11-diagnostics-roadmap-20261002`.
Reviewed starting HEAD: `763e100a963e545645157cf2cd83c5351190e072`.
Fetched base main: `cb8a4890d5e1052123487328499f94e6c994a40d`.

This pass changes documentation and reusable engineering workflow guidance only.
It does not implement A11, change production/package/test bytes, introduce a
provider, perform model requests, or merge the PR. A11a/A11b/A11c remain NOT
STARTED. A2.3 remains unimplemented; M6 remains DEFERRED.

The independent [Claude review](llmtool_a11_diagnostics_roadmap_review_20261002.md)
reviewed the earlier `fb56e20` snapshot and concluded APPROVE with recommendations
before implementation. That historical report remains unchanged. Several of its
recommendations had already landed by `763e100`; the table distinguishes those
from this follow-up. No claim is made that Claude reviewed these new edits.

## Findings and corrections

| Finding in current documents | Correction and acceptance boundary |
| --- | --- |
| Approval appeared after execution/repair in two workflow diagrams | Policy and required approval precede every side effect, including tests, repair and Git; changed action/state invalidates approval. |
| A11a said no file mutation while also introducing persistence | Separate project mutation from explicitly opted-in private diagnostics writes; all existing read-only/text-only commands default to no new persistence. Setting a store path does not enable logging. |
| New diagnostics could contaminate stdout/JSON or imply saved traces that do not exist | Preserve existing output schemas, empty-stdout failures and command-specific exit behavior, including chat interrupts; distinguish ephemeral identifiers from persisted traces. |
| A11a fallback events/policy could be mistaken for a new routing engine | A11a emits actual diagnostics only; recovery is A11b scope, requires explicit profile/data policy, and does not silently alter A9/A10 commands. |
| Retention and log failures were underspecified | Document opt-in defaults of 100 runs / 64 MiB total / 14 days / 1 MiB per run; require bounded events, concurrency-safe capacity accounting, safe cleanup and explicit write-failure behavior. These are planned contracts, not implemented features. |
| cwd restriction could be mistaken for execution isolation | Future executor must enforce filesystem/network/descendant boundaries; unsupported isolation fails closed. Typed actions, exact approvals, audit availability and safe abort precede mutation. |
| Current README still claimed A10 feature branch / pending review | Correct both stale paragraphs and the roadmap cross-reference; keep historical reports intact. |
| Root skills index omitted validate-runtime and retained an A2.6 stop marker | Add existing skill to index and use the generic next-milestone review boundary. |
| Workflow guidance duplicated runtime safety rules and demanded builds for docs | Link implementation/runtime workflows to the central review checklist; documentation-only edits use documentation validation. |

## Claude review disposition and design decisions

| Claude recommendation | Disposition | Final decision / evidence |
| --- | --- | --- |
| 1 — Split A11 into separately reviewable milestones | ACCEPTED; split already present | Add explicit status/dependency/gate table. A11a must merge before A11b; A11c requires both. Each needs its own report/review. |
| 2 — Decide persistence defaults and writing contract | ACCEPTED; sharpened here | Choose default no new writes for existing commands, metadata-only explicit opt-in, no raw-content retention in A11a. README mirrors roadmap. |
| 3 — Fallback data-governance boundary | ACCEPTED; policy already present | Explicit source/destination/data authorization, no brand-based trust inference. A11a does not implement routing; A9/A10 retain fail-fast semantics. |
| 4 — Sandbox, Git hardening, approval, patch recovery | ACCEPTED; constraints strengthened | Preserve A5 read-only protection; future mutation requires its own reviewed executor, real isolation, pre-action approval and audit. Rollback is scoped and stops on external changes. |
| 5 — Resource/retention budgets | PARTIALLY ACCEPTED | Concrete planned store defaults added; per-event bound, executor provider-call/deadline/repair values and repeated-action rule remain mandatory implementation-spec decisions. No unsupported cost/token estimates become hard guarantees. |
| 6 — Store security/concurrency/platform policy | ACCEPTED | Retain private permissions, symlink rejection and trusted setting boundary; specify active-run/unrelated-file preservation and capacity accounting. Platform support must be demonstrated before claiming it. |
| 7 — Exit-code compatibility | ACCEPTED; expanded | Preserve existing status codes plus documented command exceptions; diagnostics cannot silently change stdout/JSON schemas. |
| 8 — Commit messages / duplicate skill rules | PARTIALLY ACCEPTED | Keep published history intact; use a descriptive new commit and central checklist links. No squash/rebase/force-push merely to rename old commits. |

## Validation

Documentation checks cover UTF-8, fences, local Markdown link targets, skill
frontmatter, skill-index completeness, diagnostics/output boundaries, milestone
consistency and intended changed-file scope. Actual result: **PASS — 7 Markdown
files, 47 local link targets, 7 skill frontmatter/index entries**; all milestone,
output/policy and scope assertions passed. `git diff --check`: **PASS**. Byte diff
against the reviewed starting HEAD confirmed production/tests and the independent
Claude report are unchanged. The complete follow-up diff was reviewed.

Application unit/full suites, wheel builds and live gateway tests are intentionally
not repeated: production, package resources and tests are unchanged. This is
consistent with AGENTS.md's documentation-only validation rule. Existing CI
results are historical evidence; this report does not claim a final-head CI pass.

## Remaining implementation decisions

Before A11a implementation, its spec must fix actual opt-in CLI/config keys,
event/schema versioning and per-event bounds, supported platform/store locking
and atomic-write behavior, plus the first instrumented flow and coverage matrix.
Before A11b, review executor isolation support, typed action allowlist, concrete
provider/repair/deadline budgets, approval expiry/state binding and audit failure
recovery. Those gates prevent roadmap text from being treated as implemented
security. Raw prompt/response retention needs a separate future design.

Known unrelated untracked benchmark/reasoning reports are preserved. No worktree,
clone, alternate checkout or gh command was used. PR #13 remains unmerged and the
new documentation changes await external review.
