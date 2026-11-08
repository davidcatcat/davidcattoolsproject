# A10 — Dynamic Context Retrieval validation

Date: 2026-10-02. Canonical repository only:
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a10-dynamic-context-retrieval`.
Fetched base main: `3923f7a5903cf7c370e587de8d9b4d2896239792`.
A0–A9 and Post-A9 hardening remain DONE and merged. A10 is implemented on this
feature branch, NOT merged; independent Claude verdict APPROVE WITH CHANGES;
follow-up fixes below, external final review / Claude re-review **PENDING**. A11 NOT STARTED;
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
2. Resolve BOTH explicit persisted planner and synthesizer profiles with readiness
   required, before any full task read, credentials or provider request.
3. Read/bound task and build the sorted metadata-only candidate manifest.
4. Resolve that profile's configured credential, create its existing text provider,
   and call complete exactly once with task + path/size metadata.
5. Validate the ENTIRE strict selection before any selected file content read.
6. Read only selected manifest paths, fully validating all content and bounds.
7. Use the preflighted synthesizer config, then resolve its configured credential/client;
   complete exactly once with task + relative path/normalized content/transform flag.
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

Selected evidence is frozen typed relative path/normalized content/original-byte
byte_count/SHA-256/control_escaped. Safe UTF-8 content is unchanged; unsafe textual
controls/bidi characters are visible escapes. SHA and size always describe original
bytes; NUL/invalid UTF-8 fail. No raw terminal controls are forwarded.
Public frozen result contains scopes, explicit profile names, selected path/size/hash
metadata plus control_escaped, A8 allowlisted runtime metadata and A4 packet. Raw selected documents are
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
Paths reject all C0, C1, bidi controls and Cf formatting. Task text retains general
unsafe-control rejection. Retrieved evidence preserves LF/CR/tab and ordinary
Unicode/Cf (including ZWJ/ZWNJ), with unsafe C0/C1/bidi visibly escaped; NUL and
invalid UTF-8 remain failures.

## Initial implementation offline validation (historical, before independent review)

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

## Initial implementation wheel validation (historical)

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

## Initial implementation company-gateway smoke (historical)

Gateway already reachable without credential/VPN/login changes:
http://home-llm.example:8000, route/model gw, enable_thinking=false.
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

## Initial implementation Git checkpoint (historical)

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

## Independent review disposition and design differences

Follow-up base branch HEAD: `80660497130f25fe367ae744bed39e2028715def`.
Freshly fetched origin/main remains `3923f7a5903cf7c370e587de8d9b4d2896239792`;
no main merge was needed. Existing PR #12 remains the review vehicle; no gh/new PR/
branch/worktree/clone/merge. The independent historical report
[Claude A10 review](llmtool_a10_dynamic_context_retrieval_review_20261002.md)
is unchanged. Claude's findings are valid observations; differences below concern
the final engineering scope, not a claim that the reviewer was wrong.

| Finding | Claude finding / suggestion | Final disposition | Engineering decision, architectural reason and evidence |
|---|---|---|---|
| A | Planner lacks size guidance; oversized/binary candidates cause avoidable post-call failure. Suggested exclusion/marking, including possible initial-byte binary inspection or skipping unreadable selections. | FIXED | Exclude regular st_size >256 KiB and narrow obvious binary suffixes using metadata/name only. Prompt states 8-file, per-file 256 KiB and total 512 KiB limits, intended UTF-8 evidence and exact candidate paths. Validate selected manifest sum <=512 KiB before read_selected. Content sniffing rejected: metadata-only discovery must not read unselected contents. Whole invalid selection still fails; no partial skip/recovery. Metadata/content-open traps, budget-order tests and mixed-directory live case pass. |
| B | Common colored logs fail on controls; suggested visible escaping with metadata. | FIXED | Accepted. Bounded raw read → original-byte SHA → UTF-8 decode → NUL rejection → deterministic visible C0/C1/bidi escaping. LF/CR/tab and normal Unicode/Cf are unchanged. Typed control_escaped marks normalized model content; original byte_count/SHA retained, no raw-document result dump. No ANSI interpretation/deletion. Plain/ANSI/C1/bidi/Unicode/provenance tests and ANSI live case pass. |
| C | Late synthesizer readiness wastes planner cost and retrieval work. | FIXED | Fully accepted. BOTH persisted profiles resolve readiness before full input consumption, credentials or provider requests. Planner credential follows task/manifest; synthesizer credential/client stays deferred until valid selection and all reads succeed. Both real-pipe readiness failures, exact event-order tests and broken-synth live zero-request preflight pass. |
| D | Two live analyze-log questions missed log_safety.py; suggested better guidance and possible previews/required evidence. | DEFERRED | Observation accepted as path-only, single-round selection-quality limitation. README advises narrow scopes and likely filenames in task. No head snippets/content preview/search/index/embeddings/recursive planner/retry/fallback. This preserves the intentional metadata-only A10 MVP; no selection-quality PASS claimed. Static architecture/no-tool checks and live results measure transport/validation only. |
| E | Malformed synthesizer output loses diagnostic raw data; suggested opt-in --save-output FILE. | REJECTED FOR A10 | Retain sanitized stderr and no partial stdout/raw malformed output. Writing output would add filesystem mutation, path/overwrite/symlink/race authorization and potentially sensitive retention/cleanup contracts to a read-only milestone. Possible future diagnostic design, not implemented. Existing parser/AST and malformed-output tests enforce no writes/dump. |
| F | Credential exclusions narrow; suggested more locations and sharing A6 sensitive_path. | PARTIALLY ACCEPTED | Add .config/gh, .config/gcloud, .cargo/credentials(.toml), .htpasswd, kubeconfig, exact credentials.json and *.tfstate/*.tfstate.backup. Reject direct A6 list reuse: broad command-risk config/secret/credential matching would exclude legitimate config.py/secret_manager.py/configuration_test.py. Positive/negative path and root-rebasing tests pass. Denylist remains defense in depth, not content-based secret detection. |
| G | macOS /tmp symlink refusal message unclear; suggested physical-path guidance. | FIXED | Accepted static sanitized physical-directory error with /private/tmp instead of /tmp guidance. No host path echo and no symlink-policy relaxation. Symlink and sanitized-error regressions pass. |
| H | benchmark.normalize_runtime and private config._provider_name imports are shared-helper debt. | DEFERRED | Technical debt recorded. No broad A8/A9/config helper extraction during A10 safety convergence; A/B/C do not require it. Inherited imports unchanged, affected shared suite and full suites pass. |

### Final eligibility, normalization and ordering contract

Eligible manifest excludes st_size >262144 and these case-insensitive suffixes:
.png/.jpg/.jpeg/.gif/.webp/.ico/.pdf/.zip/.gz/.bz2/.xz/.7z/.woff/.woff2/.ttf/.otf/
.mp3/.mp4/.mov/.sqlite/.sqlite3. No content sniffing. Extensionless or misleadingly
named binary files may still reach bounded retrieval and fail UTF-8/NUL checks;
this is explicitly not complete binary detection. Eligible exclusions are not
file truncation; aggregate manifest/scan overflows still fail, asking for narrower scope.

Model-facing controls become visible escapes (ESC → literal backslash+x1b,
C1 similarly xNN, bidi uNNNN), with semantic text retained. Normalization uses
the established terminal escape helper but explicitly preserves CR as well as
LF/tab. Original raw per-file/total budgets remain 256/512 KiB. Escaped UTF-8
content is bounded to at most 4x original byte_count; existing 4 MiB provider
payload and 2 MiB result JSON limits still fail closed, without truncation.
The reader computes original-byte size/hash; frozen models validate field formats,
untransformed hash equality and normalized content controls/bounds. Hash is audit
provenance, never an authorization primitive. Public metadata adds the stable
boolean control_escaped; full raw or normalized documents are not repeated there.

Final order: root/scopes authorization → nonblocking explicit-source validation →
planner readiness → synthesizer readiness → full task read → metadata-only eligible
manifest → planner key/client/one call → whole strict plan including total-size
validation → selected bounded reads/normalization → synthesizer key/client/one call →
A4/A6/source/result validation → stdout. Preflight snapshots are reused; no generic
request/project overrides, no process-environment mutation in production.

### Follow-up offline tests, wheel and gateway evidence

Final measured counts and wheel hash are recorded below. Existing A0–A9 tests
are unchanged. Old A10 tests changed only where A/B/C intentionally alter behavior:
normalization/metadata schema, eligibility instead of oversized candidates, and
both readiness checks before planner key/input. Prior size/race/strict-parser/
source/no-output/no-execution assertions remain; direct read_selected size-limit
test still checks its defense even though internal manifests now exclude oversize.
Eight new regression tests cover A/B/C/F/G; no old tests were weakened to hide a failure.

Live follow-up used isolated temporary HOME/config and synthetic files only,
company http://home-llm.example:8000 route gw, enable_thinking=false, no credentials or
VPN/login changes. Three cases, FOUR total model requests (not fourteen), no retries:

| Case | Manifest / selected | Provider calls | rc / stdout bytes | Result |
|---|---|---|---|---|
| ANSI-only | color.log, 77 original bytes; selected that file | planner 1, synthesizer 1 | 0 / 2591 | PASS. Synth payload verified visible backslash+x1b, no raw ESC; control_escaped=true. Original SHA a462479ae5a43496f5d53d10b4c7b231862288854bd0e02af20bce99538e4dda. Strict A4/source valid. |
| Mixed | oversized log (524289 bytes), binary diagram.png, plain.log (67 bytes); manifest/selection only plain.log | planner 1, synthesizer 1 | 0 / 2344 | PASS. No oversized/binary candidate contents opened; plaintext unchanged, control_escaped=false. Strict A4/source valid. |
| Broken synth | ready company planner + incomplete broken synthesizer | ZERO | 1 / 0 | PASS preflight. No credential/client/request, no retrieval. Sanitized provider base_url is not configured error. |

Both live requests show events ready/ready before key/planner, retrieval before
synthesizer key. stderr empty for successful cases. All key lookups returned None.
Execution was trapped; no commands/Git/tools/tests were run by model output. No
model-quality score or generalized selection-accuracy claim. No MODEL-OUTPUT FAIL
occurred in these attempts; no repeated request to chase an answer.

Security review: complete final production diff checked for content-before-planner,
sniffing, unbounded reads, raw unsafe controls, hash mismatch, early credentials/
providers, retry/fallback, file writes, shell/Git/tools, secret logging, absolute
path leakage, partial stdout, environment mutation, A9 changes and A11 scope.
No such execution/mutation mechanisms added. AST and runtime execution traps pass;
authorization still comes from deterministic explicit scopes and manifest only.
Known limitations remain misnamed binary/secret content, path-selection quality,
provider context limits and non-atomic hostile-filesystem races. M6 DEFERRED;
Anthropic A2.3 unimplemented; A11 NOT STARTED; PR #12 NOT merged.

### Final follow-up measured results

| Check | Python | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
|---|---|---:|---:|---:|---:|---:|---:|
| Focused A/B/C/F/G | 3.13.3 | 8 | 8 | 0 | 0 | 0 | 0.051 s |
| Full A10 | 3.13.3 | 53 | 53 | 0 | 0 | 0 | 0.704 s |
| Affected shared | 3.13.3 | 258 | 258 | 0 | 0 | 0 | 19.166 s |
| Installed-wheel full A10 | 3.13.3 | 53 | 53 | 0 | 0 | 0 | 0.640 s |
| Full canonical syntax + suite | 3.13.3 | 605 | 604 | 0 | 0 | 1 | 47.660 s suite |
| Full canonical syntax + suite | actual 3.9.6 | 605 | 604 | 0 | 0 | 1 | 75.789 s suite |

Both full canonical checks exit 0. The single skip remains the inherited
non-UTF-8 filename host-filesystem test. Development Python is 3.13.3; /usr/bin/python3
is actual 3.9.6. No CI status inferred from local tests; external final-head CI review pending.

Follow-up wheel SHA-256: `449686297df8012be2bd7ef643d7e3f6ce5a4d45cc29279be099584aadcacac1`.
PASS: 32 production modules + 3 unchanged runtime skills, tests/repo examples
excluded. All wheel/source/installed package bytes match. Installed full A10 tests
assert import origins under /private/tmp/llmtool-a10-followup-installed, not source.

Commands (all launched from canonical root):

~~~sh
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-tests.py followup
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-tests.py focused
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-tests.py shared
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
tools/llmtool/.venv/bin/python -m pip wheel --no-deps --wheel-dir /private/tmp/llmtool-a10-followup-wheel tools/llmtool
tools/llmtool/.venv/bin/python -m pip install --no-deps --no-index --target /private/tmp/llmtool-a10-followup-installed /private/tmp/llmtool-a10-followup-wheel/llmtool-0.1.0-py3-none-any.whl
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-review-wheel-verify.py
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-tests.py focused /private/tmp/llmtool-a10-followup-installed
tools/llmtool/.venv/bin/python /private/tmp/llmtool-a10-review-live.py
git diff --check
~~~

### Follow-up Git checkpoint

Production/test fix commit: `ae2ddbea0ba2d6726dcaa55f5767e1a99c1ecd1b`.
Final documentation follows separately; final pushed HEAD is reported in the
completion checkpoint. Six follow-up task paths: retrieval.py, retrieval_files.py,
test_retrieval.py, README.md, ROADMAP.md and this validation report.
Canonical tracked working tree will contain only those committed changes; six
known unrelated untracked benchmark/reasoning paths were untouched/unread/unstaged.
Independent Claude review report unchanged (git diff --quiet verified).
Documentation UTF-8/links/fences/whitespace/README CLI parser checks PASS;
unstaged and scoped staged git diff --check PASS. No gh, PR creation/merge,
alternate branch/checkouts/worktree/clone or A11 work. PR #12 final review and
Claude re-review remain pending; no final-head remote CI result claimed.

## Final VPN live acceptance — 2026-10-02

Validated the real canonical-source `llmtool retrieve-context` CLI/runtime on
`feat/a10-dynamic-context-retrieval` at
`660e389e3707bcd4e480755728cf32112afa55b0`. Company VPN gateway
`http://home-llm.example:8000` was reachable. Requests used the persisted isolated
`company` openai-compatible profile, model `gw`, and
`provider_options: {enable_thinking: false}`. No API key or Authorization header
was used. Temporary HOME/config and synthetic evidence were created under a
physical `/private/tmp/llmtool-a10-final-live-*` directory and removed by the
runtime fixture cleanup. Real user configuration and credentials were not read
or modified.

| Required case | Actual result | Real gateway requests |
| --- | --- | --- |
| ANSI log | PASS; rc=0; 2.263 s; planner selected `app/color.log`; strict A4 packet validated and all three evidence sources were exactly `app/color.log` | 1 planner + 1 synthesizer |
| Eligibility filter | PASS; rc=0; 2.285 s; manifest contained only `app/plain.log`; planner selected it; strict A4 packet validated and all three evidence sources were exactly `app/plain.log` | 1 planner + 1 synthesizer |
| Broken synthesizer readiness with open stdin pipe | PASS; rc=1; 0.010 s; sanitized error `provider base_url is not configured`; stdout empty | 0 |

The ANSI fixture contained real ESC bytes around a database authentication error,
followed by connection-refused and retry-failed text. Retrieved metadata was
`control_escaped=true`, original `byte_count=78`, and original SHA-256
`67883446489afaa82d6f6b1908a4fc44b1b9ae285251dbea3a0ca96e6ab2c58b`.
Instrumentation verified the synthesizer received visible literal `\x1b`
sequences, no raw ESC, and otherwise preserved the fixture text. Public JSON
metadata matched the original-byte size/hash and retrieval records.

The eligibility fixture included a 70-byte UTF-8 `app/plain.log`, a 262145-byte
`app/big.log` (above MAX_FILE_BYTES), and `app/diagram.png`. The actual planner
payload contained only the plain log's relative path and byte count. Descriptor
instrumentation verified no candidate content was opened during manifest
construction and only selected `app/plain.log` was opened during retrieval;
excluded big/binary fixtures were not read. Plain-log metadata preserved
`control_escaped=false`, original byte count 70, and SHA-256
`5ddad2bea56d6689981ab47b817ad68d951c1d9a5526e71fa8a752f5f73e2a4c`.

For preflight, the real persisted `broken` profile lacked base_url. A real pipe
had its writer held open with no bytes and no EOF. Events stopped after planner
and synthesizer readiness resolution: no full input read, credential lookup,
client construction, file retrieval, or gateway request occurred. Both successful
cases resolved both profiles before input/credentials; synthesizer credential
lookup occurred only after retrieval.

The one-time acceptance harness called the real CLI and actual provider HTTP
transport without simulating model replies. It counted both Provider.complete
calls and HTTP POSTs, inspected outgoing payloads, verified absence of
Authorization, and trapped subprocess/os.system execution attempts. Total real
gateway requests: **4**. MODEL-OUTPUT FAIL: **none**. No retries, fallback,
shell/Git/tool execution, provider substitution, or production modifications
occurred. Optional real-repository selection observation was not performed; only
synthetic evidence was sent.

Post-live focused A10 tests on Python 3.13.3: **53 discovered, 53 passed, 0 failed,
0 errors, 0 skipped**, elapsed **0.655 s**. `git diff --check` passed. No production
or package bytes changed, so wheel and full Python suites were not repeated in
this documentation-only pass; prior exact-head validation remains separate
historical evidence.

Claude final re-review verdict: **APPROVE**. Accepted non-blocking follow-ups
remain **N1** (excluded-candidate visibility/zero-selection UX), **N2** (narrow
binary-extension coverage), and **N3** (reproducibility of one-time validation
helpers). None was expanded into production work here. The framework evidence
supports the A10 merge gate; this is not a model-quality score. PR #12 remains
unmerged, A11 remains NOT STARTED, M6 remains DEFERRED. No worktree/clone was
created and no gh command was used. Claude's review/re-review reports and the
known unrelated untracked benchmark/reasoning reports were left untouched.


## Final merge status — 2026-10-02

External ChatGPT final merge review: PASS.

Final reviewed branch head:

`77004c69574f213a7619dfec6353b25a8842a851`

Exact-head GitHub Actions run `36915340789`: SUCCESS. Python 3.9 and
Python 3.14 each ran 605/605 tests PASS; Offline canonical checks and
Working tree is clean both succeeded.

The final VPN live acceptance on the preceding production-equivalent head recorded:
- ANSI/control normalization PASS with original-byte provenance preserved.
- Oversize/obvious-binary manifest eligibility PASS without candidate-content sniffing.
- Broken synthesizer readiness preflight PASS with zero provider requests.
- Four total real company-gateway requests, no retries/fallback/tools/Git/shell.
- Claude final re-review verdict: APPROVE.

PR #12 was marked ready for review and merged normally to `main` with merge commit:

`21b9757f0ea5abfb06a7a07dc12430f036f6e395`

A10 Dynamic Context Retrieval is DONE and merged.

Accepted non-blocking follow-ups remain:
- N1: excluded-candidate visibility / zero-selection UX.
- N2: broader obvious-binary suffix coverage.
- N3: reproducibility of one-time validation helpers.

M6 remains DEFERRED. Anthropic HTTP A2.3 remains future work.
A11 Production Workflow is NOT STARTED and is the next main milestone.
