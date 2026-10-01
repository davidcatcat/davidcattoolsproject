# A10 — Dynamic Context Retrieval validation

Date: 2026-10-02. Canonical repository only:
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a10-dynamic-context-retrieval`.
Fetched base main: `3923f7a5903cf7c370e587de8d9b4d2896239792`.
A0–A9 and Post-A9 hardening remain DONE and merged. A10 is implemented on this
feature branch, NOT merged; external ChatGPT review **PENDING**. A11 NOT STARTED;
A12 remains future optional frontend. M6 remains DEFERRED, with A8 exact curated
matching and historical Claude model safety FAIL unchanged.

## Architecture and execution boundary

`retrieve-context` requires explicit root, one or more scopes, planner profile and
synthesizer profile. The new modules are `retrieval_files.py` (authorization,
metadata manifest and read-only evidence adapter), `retrieval.py` (two-pass data
flow and typed results), and `retrieval_cli.py` (parser and complete-output boundary).
Existing `cli.py` receives only an additive parser/import/dispatch integration.
No A9 role, plan or orchestration behavior is changed.

1. Validate physical root/scopes and task-source conflicts without consuming stdin.
2. Resolve the explicit persisted planner profile with readiness required.
3. Read/bound task and build the sorted metadata-only candidate manifest.
4. Resolve that profile's configured credential, create its existing text provider,
   and call complete exactly once with task + path/size metadata.
5. Validate the ENTIRE strict selection before any selected file content read.
6. Read only selected manifest paths, fully validating all content and bounds.
7. Resolve the synthesizer profile with readiness required, then its configured
   credential/client; complete exactly once with task + relative path/exact content.
8. Reuse A4 parse_context_packet and A6 structural validation. Enforce supplied
   evidence-source membership. Build the fully bounded result before stdout.

Planner schema: exactly `{"files":["relative/path"]}`, with unique exact manifest
paths, 0..8 entries. Duplicate keys, constants, extra fields, invalid UTF-8, prose,
fences, bad nesting, duplicate/unknown/absolute/traversal/noncanonical paths fail.
Zero files is valid and still permits the task-only synthesizer call.

Exactly two sequential provider calls on success. No retries, fallback, parallel
calls, recursive retrieval, provider substitution, A9 integration, indexing,
semantic search, embeddings, RAG, vector DB, file watcher, Git history or web search.
Provider != Role != Retrieval Planner. Existing transports remain text-only;
capabilities grant no permission. No command/VERIFY_NEXT/tool execution, file/code
writes, shell, Git invocation, patch application, test execution or approval engine.

## Authorization, credentials and provenance

Explicit root/scopes authorize sending selected evidence to the configured
synthesizer. Planner output is never authorization and cannot enlarge the manifest.
Root and ancestors are real physical directories; no symlink roots/scopes, no
symlink directory traversal or file following. Regular files only. POSIX anchored
directory descriptors, no-follow/nonblocking opens, lstat-equivalent checks and
fstat identity comparisons protect before/open/after reads. Detected changes or
substitutions fail before synthesis. macOS/Linux no-follow APIs are required;
unsupported platforms fail closed. Physical temporary paths use `/private/tmp`.

`.git` content, including roots within `.git`, is excluded. Narrow credential-file
policy excludes .env/.env.*, .netrc, .npmrc, .pypirc, .pgpass, .git-credentials,
.vault-token, conventional private-key basenames and .pem/.key/.p12/.pfx files;
credential stores .ssh/.password-store/.aws/.kube/.docker/.gnupg/.azure and
.config/llmtool are excluded, including roots inside them. This does not reuse A6's
broad sensitive-display name policy: config.py, configuration_test.py and
secret_manager.py remain retrievable. Known etc/shadow, etc/gshadow and proc/<pid>/environ locations remain excluded
even when root is rebased into their parent directories; hosts/status nearby paths
remain eligible. Explicit excluded scopes fail instead of
creating a misleading empty authorized scope. Nested excluded entries are omitted.

Both explicit profiles use existing resolve_profile_config(require_configured=True).
Generic LLMTOOL_* and cwd project overrides cannot rewrite endpoint/model/type or
settings; normal ask/config precedence is unchanged. Configured api_key_env value
lookup still occurs after readiness. No credential discovery, real HOME scanning,
environment mutation, config mutation or new provider is introduced by A10.

Selected evidence is frozen typed relative path/content/UTF-8 byte count/SHA-256.
Public frozen result contains scopes, explicit profile names, selected path/size/hash
metadata, A8 allowlisted runtime metadata and A4 packet. Raw selected documents are
not repeated in result JSON. The model-produced packet can itself quote evidence.
Unavailable token counts remain null; no estimation/ranking/quality score.
Each evidence source must exactly equal `task` or an actual retrieved relative path;
invented sources fail without repair. Planner output/reasoning is not evidence.

Task, filenames and documents are untrusted data. Structured JSON handoff reduces
ambiguity but is not claimed to prevent prompt injection. Human output reuses
terminal escaping; JSON preserves generated/user-supplied packet material.
Failures emit sanitized stderr with nonzero exit and no partial stdout.

## Final bounds

| Item | Bound |
|---|---|
| Task UTF-8 | 1 MiB; inherited input reader outer limit 16 MiB |
| Explicit scopes | 32 before deduplication |
| Path UTF-8 / component depth | 4096 bytes / 64 |
| Directory entries visited | 16,384, including excluded entries |
| Manifest files / compact JSON UTF-8 | 4096 / 256 KiB |
| Planner response UTF-8 / JSON depth | 64 KiB / 2 |
| Selected files | 8 |
| Each file / total evidence | 256 KiB / 512 KiB |
| Each selected read | at most 256 KiB + 1 to detect overflow |
| Structured provider user payload | 4 MiB including escaping |
| ContextPacket | inherited A4 1 MiB / depth 4; A6 1024 total items |
| Result JSON UTF-8 | 2 MiB |

No truncation. Manifest overflow asks for narrower scopes. Scan/depth/scope caps
bound metadata traversal, including large excluded/empty directory trees; message
and result caps allow bounded JSON escaping without weakening the A4 packet cap.
Paths reject all C0, C1, bidi controls and Cf formatting. General task/evidence text
preserves LF/CR/tab and ordinary Unicode formatting including ZWJ/ZWNJ, while
rejecting NUL, unsafe controls, bidi formatting and invalid UTF-8.

## Offline validation

All commands launched from canonical repository. Existing A0–A9 test bytes were
unchanged; only new test_retrieval.py was added. No CI secrets or live provider
dependency added to canonical tests. Mock providers/local HTTP fixtures only.

| Check | Python | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
|---|---|---:|---:|---:|---:|---:|---:|
| A10 focused | 3.13.3 | 45 | 45 | 0 | 0 | 0 | 0.608 s |
| Affected shared | 3.13.3 | 258 | 258 | 0 | 0 | 0 | 18.378 s |
| Full canonical syntax + suite | 3.13.3 | 597 | 596 | 0 | 0 | 1 | 47.439 s suite |
| Full canonical syntax + suite | actual 3.9.6 | 597 | 596 | 0 | 0 | 1 | 74.499 s suite |
| Installed-wheel A10 focused | 3.13.3 | 45 | 45 | 0 | 0 | 0 | 0.482 s |

Inherited skip: test_git_intel.GitFixture.test_unusual_filename_bytes_roundtrip,
`host filesystem/sandbox rejects non-UTF-8 filename bytes`. Not an A10 regression;
the old test was not weakened. Both canonical commands exit 0.

Commands:

```sh
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-tests.py focused
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-tests.py shared
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

Shared modules exercised: test_context, test_inputs, test_config,
test_provider_integration, test_cli, test_terminal, test_validator,
test_orchestration and test_benchmark. The full suite also exercises all inherited
provider, configure, feedback, skills and hardening tests.

Focused coverage includes required CLI args and repeated scopes; sorted/deduplicated
metadata-only manifests; manifest/count/scan/depth limits; .git/credential exclusions
and normal config.py inclusion; root/ancestor/scoped/nested symlink handling;
unsafe filenames; strict planner schema and full-selection validation before reads;
selected-only content reads; UTF-8/Cf success, binary/control failures; exact byte
limits; nonregular files; substitutions between manifest/lstat/open and changes
during read; planner substitution blocking synthesis; profile/env/project isolation;
readiness/credential/client order; real queued-pipe conflicts, real stdin success
and infinite-stdin readiness failure; zero files; one call per stage; fail-fast;
strict A4/source validation; safe runtime allowlist/null tokens; immutable bounded
stable results with no raw documents; no partial stdout; terminal safety;
injection/VERIFY_NEXT text under execution traps; AST checks against execution,
file writes and A9 coupling.

Initial focused development run exposed newline filenames accepted by the general
text policy; path-specific C0 rejection fixed this without narrowing general
Unicode text. Review also added root-inside-.git/credential-store protection and
regressions, including credential-location exclusions after root rebasing. Final
counts above were measured after those fixes.

## Wheel validation

```sh
tools/llmtool/.venv/bin/python -m pip wheel --no-deps \
  --wheel-dir /private/tmp/llmtool-a10-wheel tools/llmtool
tools/llmtool/.venv/bin/python -m pip install --no-deps --no-index \
  --target /private/tmp/llmtool-a10-installed \
  /private/tmp/llmtool-a10-wheel/llmtool-0.1.0-py3-none-any.whl
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-wheel-verify.py
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-tests.py focused \
  /private/tmp/llmtool-a10-installed
```

Wheel: llmtool-0.1.0-py3-none-any.whl.
SHA-256: `04b4a082549590a3022f64f2f5b65b449ff6fa8fbc78036312f90f1cf293ec40`.
PASS: 32 Python production modules plus 3 runtime SKILL.md resources (35 package
files). All packaged source and installed bytes match. New A10 modules present;
tests, benchmark/feedback/orchestration repository examples excluded. Runtime
resources and inherited production modules remain unchanged apart from additive
cli.py integration. Installed test runner asserts llmtool and every A10 import
originates from installed target package bytes, not source checkout; 45/45 PASS.

## One isolated company-gateway smoke

Gateway already reachable without credential/VPN/login changes:
http://10.29.7.58:4000, route/model gw, enable_thinking=false.
Test helper launched from canonical repository, importing canonical A10 source;
isolated temporary HOME/config and synthetic evidence directory only. No real user
config/auth files, project source or personal Claude/Codex/OpenAI were used.

Task: `Determine why login returns database authentication failed.` Candidate files:
auth.py, db.py, README.md, unrelated.py. One planner selected auth.py (99 bytes) and
db.py (89 bytes), in that order; only those contents reached one synthesizer.
Raw content reads and provider call order were instrumented, and shell/subprocess
execution was trapped. api_key was called after readiness twice and returned None
both times. No credential material supplied or discovered.

Result: **transport/retrieval/strict-packet PASS**, rc=0, stdout 2879 UTF-8 bytes,
stderr empty. Exactly 1 planner + 1 synthesizer request. Packet: 4 facts, 4 evidence,
2 inferences, 3 hypotheses, 3 unknowns, 3 verify_next items. Evidence sources were
auth.py/auth.py/db.py/db.py, all allowed. No shell/Git/tools execution or retry.
This does not score model quality or prove factual accuracy.

The live smoke preceded the final narrow credential-location/root-rebasing
exclusion addition. Synthetic selected paths/contents and the provider/filter flow
are unchanged; final offline regressions, both full suites and wheel were rerun.
No gateway requests were repeated for this deny-policy-only change.

The first smoke harness invocation had an incorrect config assertion and stopped
before any provider request; that harness assertion was corrected. The only model
run sent exactly two requests; no model-output retry or preferred-answer chasing.

Temporary smoke evidence hashes:

| Relative path | Bytes | SHA-256 |
|---|---:|---|
| auth.py | 99 | 79218d37d50b7b5303f7b4141847f71b0efebfd6d39973af927ed3aa5d8ef41c |
| db.py | 89 | 1edae10d4b23e6550a16078df752ba9691d1ef2e0fc11c62f7e74307fcf8f8cb |

## Security review and limitations

Complete A10 production code, additive CLI diff and regression tests reviewed.
No subprocess import, shell=True, os.system, eval/exec, Git execution/mutation,
filesystem writes, command/patch execution, provider-native tools, credential
discovery/logging, environment mutation, hidden role inference, retry/fallback,
profile substitution, semantic index, recursive retrieval or A11/A12 code added.
AST tests enforce the production execution/write boundary. File reads are bounded,
selected manifest paths only; symlinks/control paths rejected, evidence source
membership checked; no partial stdout or raw runtime/config/credential dump.
Existing A6/A8 rules and provider transports are unchanged.

Known limitations: names/sizes can lead to wrong selection; no semantic content
search. Source membership does not prove claim truth/support. Denylist is defense
in depth, not secret-content detection; ordinary source/hard-link aliases may
contain secrets. Explicit scopes authorize external provider data handling.
Descriptors/identity checks detect obvious races but do not provide an atomic
filesystem snapshot or defeat every hostile filesystem. POSIX support only.
Task source reader retains the established 16 MiB outer cap before A10's 1 MiB
task validation. Model-facing context/token windows remain provider-dependent.
Prompt instructions are not a complete injection defense; no execution path is
provided. JSON/redirected output can include model/user/evidence text.

## Git checkpoint

All validation was run on the A10 feature working tree based on the fetched main
SHA above. No alternate checkout/worktree/clone, gh command or PR creation/merge.
Historical milestone and independent review reports remain unchanged. Known
unrelated untracked benchmark/reasoning paths were neither read, edited, staged,
deleted nor moved. No external scratchpad/main-docs worktree was touched.
Production/test commit: `8dad54cec406edbf7e94fdf17840a38750dcfe5a`
(`feat(llmtool): add A10 dynamic context retrieval`). Documentation/report follow
in a separate commit. The final pushed branch HEAD is supplied in the completion
checkpoint; no CI or external review pass is claimed here.
Documentation validation PASS: UTF-8, relative-link targets, whitespace, balanced
Markdown fences and both README A10 CLI examples parsed successfully. Scoped
unstaged/staged git diff --check PASS. All eight changed task paths belong to A10.
External review remains PENDING; do not merge or start A11 automatically.
