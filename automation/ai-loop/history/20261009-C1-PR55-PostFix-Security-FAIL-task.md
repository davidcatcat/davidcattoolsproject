# ACTIVE QA TASK — PR #55 post-Medium-fix independent Security and Functional QA

## Scope and immutable authority

- This is a **read-only, source-frozen, separate two-role QA task**, NOT another Codex implementation, report repair, or PR merge.
- [PR #55](https://github.com/davidcatcat/davidcattoolsproject/pull/55), existing branch `feat/llmtool-c1-test-excerpts-20261009`, **exact HEAD `d5ecfce9f7e28da2983affb235dd942d51388723`**, base `8e29024656f873431f6ad83e45015b57bfe381a9`. Draft state required. Changes to HEAD, branch, base or 7-file inventory STOP.
- Original PR HEAD `67d02be6bc8cb3f237c5060edda3321f6d885bf3` was reviewed with Security 2 Low + **1 Medium** / Functional QA 5 Low. These prior role PASS statuses are **STALE** on revised source. [Issue #56](https://github.com/davidcatcat/davidcattoolsproject/issues/56) reports the old Medium INPUT_VALIDATION finding as FIX_BEFORE_MERGE at `test_output_excerpts.py:46`.
- Codex's one-candidate fix advanced to implementation HEAD `85d8f9fa11880b6be45a4b21c7521d6a0168d2f0` and publication `d5ecfce9f7e28da2983affb235dd942d51388723`. See runner-owned report `reports/ai-loop/C1-PR55-Medium-InputValidation-Fix-20261009-20261008T213245Z-37499.md`.
- The completed Medium repair task is archived under `automation/ai-loop/history/20261009-C1-PR55-Medium-InputValidation-Fix-task.md/json`. Do not replay the repair, or older `--resume` or original C1 implementation tasks.
- `task.json` pins **exactly seven files**: five original product/test/README paths plus two runner-owned reports. No other tracked/untracked files can be modified by this task. This is `mode=review_existing_pr`, `max_repairs=0`, `qa_gates=[security,functional_qa]`.

## Fresh independent Security gate — first session

Independently inspect **actual** new implementation, test changes and the entire cumulative PR diff. Primary focus: previous Medium resource-consumption risk in URL credentials regex (input validation), replacement `_URL_SCHEME` / `_URL_AUTHORITY` / `_sensitive_url`, and `_SENSITIVE.search(text) or _sensitive_url(text)`.

Adversarially reason about input sizes ≤1 MiB, overlapping schemes, arbitrary scheme/username/password lengths, Unicode regex case behavior, bounded time without catastrophic backtracking, true credential-bearing URL rejection, benign no-secret URLs, no plaintext content/output leak on blocked captures, and fail-closed behavior. Independently verify prior security boundaries: physical no-follow input path traversal, symlinks/hardlinks/FIFO/parent swaps, byte limits/UTF-8/ANSI/bidi and identity race, no unintended provider/network/config/shell/Git mutations, provenance and failure exit integrity. Provide concrete source/test evidence; no unsupported exploit claims.

**Any Medium, High, Critical or `blocking=true` finding, even if model status is PASS, must terminate the specialized gate as STOPPED, keep PR Draft and await coordinator disposition.** A clean Security verdict alone is not full QA or permission to merge. The prior Medium issue must be examined explicitly, never assumed fixed because deterministic tests passed.

## Fresh independent Functional QA — second session only when Security clean

Assess public CLI `llmtool test-excerpts --file ... --exit-code ... --json`, actual unittest/pytest fixtures and edge cases, repeated/parametrized IDs, collection errors, malformed/unsupported data, exit code preservation, partial/INCOMPLETE and omission/source SHA/byte offsets; test near 1-MiB stress input in 5-second subprocess probes on real interpreter versions and no secret-bearing URL false-negatives after the fix. Verify harmless URLs remain COMPLETE, error categories are fixed and no file/provider side effects occur. JUnit XML remains unsupported under narrow C1 scope and cannot be counted as delivered.

Again, **any Medium+ or blocking functional finding prevents full PASS**. Low findings must be retained for follow-up; no broadening to implement new features, changing models or token-savings claims.

## Fresh deterministic and GitHub gates

- Runner preflight `--check` without model calls, validates exact PR identity and clean canonical Mac checkout. Exact new HEAD GitHub CI [#37849174251](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37849174251) completed success Python 3.9 and 3.14, **1292 tests each, 1 skip**.
- Re-run related unit/syntax suite and **both local Mac full `make check`** scripts on immutable HEAD; stop before spending model tokens on failed test/gate.
- Invoke two new read-only Claude sessions with separate role prompts, no commands or mutations; each verdict bound to HEAD `d5ecfce9f7e28da2983affb235dd942d51388723` and independent local evidence. Stop before second model call when first returns any Medium+ or blocking finding.
- Publish **only fixed sanitized** status, finding counts and allowlisted file paths to [Issue #56](https://github.com/davidcatcat/davidcattoolsproject/issues/56). Full role details/output remain private under `.ai-loop/<run>`, and are never copied to GitHub. Runner does not modify/commit/push files or merge.
- A dual model-assisted QA PASS is not external human certification or final merge authorization; the coordinator evaluates signoff separately, with residual Low limitations recorded. No company/external provider content transfer; C1b/Phase 0 and C1c paired token/cost measurement stay separately gated.
- Only after coordinator confirms exact-head helper CI Python 3.9 and 3.14 green, operator executes one existing launcher: `bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh`. No manual Codex/Claude prompts, no arbitrary shell/Git intervention. STOP preserves candidate and private evidence.

