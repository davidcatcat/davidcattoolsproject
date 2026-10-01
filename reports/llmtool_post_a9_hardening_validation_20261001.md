# llmtool Post-A9 Hardening validation — 2026-10-01

Scope: Post-A9 hardening only. A0–A9 are DONE and merged; this hardening branch is
not merged and awaits external ChatGPT review. A10 NOT STARTED.

Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Fetched base main: `0ec19514e7e1e2d990dea93f040e3c647f8e20ca`.
Branch: `fix/post-a9-hardening`.
Production/test commit: `4f7161dc1372df931958bb10310370fef1bafac4`.

Source findings: [Claude A0–A9 verification](llmtool_a0_a9_verification_20261001.md).
That historical report is unchanged. No worktree, clone, alternate checkout or
repository copy was created or used. Existing external worktrees were inspected
only for diagnosis. Six unrelated untracked report paths were preserved.

## Disposition

| Item | Disposition | Root cause | Production change | Regression/evidence | Remaining limitation |
|---|---|---|---|---|---|
| M1 | FIXED | Stripping kubectl destination flags lost their credential-forwarding risk. | Connection/authentication/impersonation overrides impose UNKNOWN minimum; recognized delete/apply/Secret reads retain DESTRUCTIVE/MUTATING/SENSITIVE_READ. | `KubectlOverrideTests`: split/equals/attached -s, option order, namespace/context combinations, higher risk retention and approval. | Narrow text classifier; trusted kubeconfig/context is required for plain metadata SAFE_READ. No command authorization or execution. |
| M2 | FIXED | Explicit input bypassed stdin inspection, silently dropping queued piped data. | Bounded nonblocking FIONREAD snapshot for POSIX pipe/socket; regular fd, memory stream, TTY and /dev/null handling. Conflict fails before config/credentials/provider. | `InputProbeTests` uses real pipe/fd/PTY, empty live writer, EOF, queued data, file/direct input and sanitized unsupported streams; updated subprocess CLI conflict test. | Does not wait for later writer data or EOF in explicit mode; assumes stdin has not already been buffered by other code. Unsupported probing fails closed. |
| M3 | FIXED | Hardcoded prompt defaults and full replacement erased saved values. | Defaults come from existing user configuration; blank preserves fields/options/generation/active selection. Unedited profile mappings and unchanged generation mappings are persisted exactly as mappings. | `PersistedConfigTests` blank edit, add-profile, omitted defaults, explicit changes and hidden secret checks; all configure/provider-integration tests pass. | YAML formatting/comments are not preserved byte-for-byte. Explicit transport type changes reset incompatible transport settings. Optional `-` explicitly unsets. |
| M4 | FIXED | Generic layered resolve_config rewrote each explicit A9 profile with request overrides. | New `resolve_profile_config` reads only persisted user profiles plus schema defaults. A9 calls it per reached step, readiness first, then api_key/client/complete. | A/B model/endpoint/settings isolation despite env/project overrides; real two-step resolver/factory ordering; configured credential env lookup; readiness/unknown-profile fail-fast; normal ask precedence retained. | Persisted config schema is validated as a whole; credential environment values are resolved only for the reached step. No retries/fallback/parallelism. |
| M5 | FIXED | str.isprintable rejected normal Unicode separators; A4 also permitted C1/bidi. | Shared `contains_unsafe_controls`: allow Unicode whitespace/separators, reject unsafe controls. A4 and A7/A8 reuse it. | NBSP/U+3000, LF/CR/tab, C0/ESC/DEL/C1/bidi/surrogate/blank and byte caps; public feedback/benchmark JSON roundtrip; context CLI rejects C1/bidi before stdout. | No normalization or redaction of ordinary text; existing field byte limits remain. |
| M6 | DEFERRED | Exact curated preferred evidence vs semantic equivalence and prompted evidence needs an independent design decision. | No benchmark matching change. | benchmark.py / benchmark_cli.py and curated benchmark fixtures unchanged; all A8 tests pass. | Separate follow-up required; no fuzzy matching or relaxed safety criteria. |
| M7 | FIXED (narrow deterministic NEXT_COMMANDS contract) | Prompt-only restrictions did not prevent Secret/credential display suggestions. | analyze-log skill version 2 names prohibited reads and requires bounded final NEXT_COMMANDS. New pure-text guard extracts a finite supported format, rejects obvious Secret selections and sensitive-file display, known A6 sensitive/mutating/destructive commands and unsupported syntax before stdout/verbose metadata. | `AnalyzeLogSafetyTests`: namespace/global flags, qualified/comma Secret names, pipes, wrappers, credential files, unsupported/fenced/multiline formatting, controls/bounds, fail-closed CLI with execution traps. Also applies to ask --skill analyze-log. | Not a general shell parser, secret detector or model-quality proof. UNKNOWN is not certified safe. Aliases, opaque/custom programs, factual claims and sensitive material outside NEXT_COMMANDS are not comprehensively analyzed. Chat skill replies remain plain untrusted text. |
| docs | FIXED | Current README/ROADMAP contradicted merged A9, A6 availability and explicit A7 file writes. | Correct present status, configure/input/profile contracts, kubectl trust assumptions and guard limits. Historical milestone reports remain unchanged. | Complete documentation diff reviewed; local Markdown links and diff whitespace validated. | Hardening is a feature branch pending external review; A10 remains future work. |

The kubectl override set was checked against the official
[kubectl global options](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_options/).
It includes `-s/--server`, token, kubeconfig/kuberc, as/as-group/as-uid/as-user-extra,
client certificate/key, certificate authority, username/password, insecure TLS,
TLS server name, proxy URL and cluster/user selection. Unsupported syntax remains
uncertain, never a new SAFE_READ allowance.

## Architecture and execution boundary

Provider != Profile != Role != Agent != Skill != Context Packet != Git Intelligence
!= Validator != Feedback Case != Benchmark != Orchestration Plan.

A9 remains a bounded sequential pipeline with explicit role → persisted profile
bindings, original task + immediately previous output only, fail-fast, one
completion per reached step, no retries, fallback or parallelism. Its LLM validator
role cannot override deterministic A6 command-risk evidence. Configured api_key_env
values still come from the environment only after readiness succeeds; generic
LLMTOOL_* request variables and cwd project configuration are not A9 profile inputs.
Normal ask/chat/config layering remains unchanged.

analyze-log checks command strings as data only. It does not run tools, execute
VERIFY_NEXT, interpret approval language, rewrite output, mutate Git/files or apply
patches. It accepts a unique final NEXT_COMMANDS heading (plain/Markdown), 1–3
single-line bullet/numbered suggestions with optional inline backticks, or `(none)`.
Fences, additional prose/sections, malformed quoting, shell interpreter tokens,
expansions and continuations fail closed. Total response limit: 1 MiB UTF-8;
individual suggestion limit: 8 KiB. These checks are intentionally narrow and do
not certify arbitrary UNKNOWN commands or the rest of a model's narrative.

M5 retains UTF-8 byte limits and rejects C0 except LF/CR/tab, DEL, C1 and the shared
bidi-control set. Unicode spaces such as NBSP/U+3000 and normal printable text are
preserved. A4 ContextPacket now shares this policy, addressing QA's L2 inconsistency.
The prior context terminal test now uses permitted CR/Unicode and remains an exact
JSON preservation test; a separate regression rejects C1/bidi before any output.

Anthropic HTTP API remains future A2.3. No provider was added. No A10 retrieval,
A11 workflow/execution, approval engine or A12/Telegram code was introduced.

## Validation evidence

All checks were launched from the canonical repository. Providers in canonical
tests are fake/mock transports; loopback HTTP fixtures require host socket access,
not credentials or external network. Final results below are newly measured.

| Check | Python | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
|---|---|---:|---:|---:|---:|---:|---:|
| Focused hardening | 3.13.3 | 21 | 21 | 0 | 0 | 0 | 0.160 s |
| Affected shared tests | 3.13.3 | 431 | 431 | 0 | 0 | 0 | 33.055 s |
| Full canonical syntax + suite | 3.13.3 | 536 | 535 | 0 | 0 | 1 | 46.700 s suite |
| Full canonical syntax + suite | actual 3.9.6 | 536 | 535 | 0 | 0 | 1 | 69.184 s suite |
| Installed-wheel focused regressions | 3.13.3 | 21 | 21 | 0 | 0 | 0 | 0.298 s |

The one inherited skip is A5 `test_unusual_filename_bytes_roundtrip`
(`host filesystem/sandbox rejects non-UTF-8 filename bytes`),
not a hardening skip. Baseline 514 was not reused as final evidence; this pass adds
21 focused tests and one context CLI regression, yielding 536 discovered tests.

Affected shared modules: test_validator, test_cli, test_inputs, test_configure,
test_config, test_feedback, test_benchmark, test_context, test_skills,
test_orchestration, test_provider_integration, test_terminal, test_provider,
test_openai_provider, test_claude_cli_provider and test_codex_cli_provider.

Commands:

```sh
git diff --check
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_post_a9_hardening.py -v
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
tools/llmtool/.venv/bin/python /private/tmp/llmtool-hardening-tests.py shared
tools/llmtool/.venv/bin/python -m pip wheel --no-deps --wheel-dir /private/tmp/llmtool-hardening-wheel tools/llmtool
tools/llmtool/.venv/bin/python -m pip install --no-deps --no-index --target /private/tmp/llmtool-hardening-installed /private/tmp/llmtool-hardening-wheel/llmtool-0.1.0-py3-none-any.whl
tools/llmtool/.venv/bin/python /private/tmp/llmtool-hardening-tests.py installed
```

The temporary test runner loads the specified unittest modules, counts the suite,
records failures/errors/skips and monotonic elapsed time. Installed mode prepends
only the wheel installation directory and asserts llmtool.__file__ is under it;
it still launches from the canonical repository. No repository was copied there.
Final logs were retained as runtime artifacts under `/private/tmp/llmtool-hardening-*`.

An initial sandbox run could not bind mock HTTP sockets; the approved host rerun
resolved that environment limitation. An intermediate full run found the old
context C1/bidi acceptance test conflicting with the new required rejection
policy. That contract was corrected with an additional rejection regression;
the final suites above have no failures/errors.

## Packaging

PASS: standalone `llmtool-0.1.0-py3-none-any.whl`, built through the established
PEP 517 isolated setuptools procedure (no production dependency changes).

SHA256: `080a1dd265b8c8dadcf287d7c198e7d9d95b89d8c9e6f78cfdd3de00999397d2`.

All 32 packaged production/resources (29 Python modules, 3 runtime SKILL.md files)
were byte-compared with canonical source. The new log_safety.py is included; A9
roles/orchestration/orchestration_cli and all prior runtime resources are present.
21 inherited resources match fetched main byte-for-byte. The 10 intentional
resource changes are cli, config, configure, context, feedback, inputs,
orchestration, terminal, validator and analyze-log/SKILL.md. Tests, repository
feedback/benchmark/orchestration example directories and credentials are excluded.
Installed-wheel imports resolve to `/private/tmp/llmtool-hardening-installed/llmtool`,
and all 21 focused regressions pass against that installation.

## Optional company gateway smoke

NOT-TESTED: no existing company gateway profile was available in the current
runtime's persisted llmtool user configuration. The availability check printed
only a boolean and did not resolve credential values or dump configuration.
No credentials/login/VPN were changed; no gateway inference, paid OpenAI request,
personal Claude or personal Codex request was made for this pass. This is not a
claim that the gateway is unreachable or that model quality passes.

Historical Claude A8 live safety benchmark remains FAIL as measured; neither its
output nor exact preferred evidence matching was changed or rerun to seek green.

## Security review

Complete production/test/documentation diff reviewed, including new guard and
regressions. PASS for the requested hardening contracts:

- M1 never turns connection/auth/impersonation overrides into SAFE_READ; approval
  is required and recognized higher-risk operations are retained.
- M2 queued stdin conflicts stop before config/key/client access; probing has no
  blocking read, sleep, polling, background thread or descriptor flag mutation.
- M3 stored credential values are never visible prompt defaults or stdout/stderr;
  explicit config writes retain the inherited hidden-input/atomic/private contract.
- M4 uses an explicit resolver, without environment mutation; readiness precedes
  credential lookup, unknown profiles fail without fallback, later steps stop.
- M5 normal Unicode spaces work while controls/invalid Unicode and limits remain
  enforced, including human/JSON ContextPacket output rejection.
- M7 rejects the documented display suggestions before any output/metadata and
  never executes any command; execution traps are covered by offline tests.

No new subprocess/shell=True/os.system/eval/exec, model-driven Git mutation, file
mutation, automatic command/patch execution, native tools, credential logging,
secret echo, raw provider stderr, runtime environment mutation, broad SAFE_READ
expansion, A10/A11/A12 implementation or orchestration retries/fallback were added.
Inherited explicit configure/feedback writes and fixed provider transports retain
their existing scope. Tests use temporary artifacts only.

## GitHub CI / external review

Production-head CI run:
[36884162120](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/36884162120),
for `4f7161dc1372df931958bb10310370fef1bafac4`.
Both jobs completed SUCCESS. Exact required step results:

| Job | Result | Offline canonical checks | Working tree is clean |
|---|---|---|---|
| check (3.9) | SUCCESS | SUCCESS | SUCCESS |
| check (3.14) | SUCCESS | SUCCESS | SUCCESS |

The final documentation-head CI is also checked before the handoff and recorded
in the Draft PR/checkpoint. Production resources are unchanged by that docs commit.

External ChatGPT review: PENDING for this hardening pass. Keep PR DRAFT; do not
merge. M6 remains DEFERRED. A10 NOT STARTED.

## Second-round independent review follow-up — 2026-10-02

This section supersedes the first-round behavior claims above; historical evidence
is retained. Source: [independent Claude review](llmtool_post_a9_hardening_review_20261001.md),
verdict REQUEST CHANGES for `b196bb34c9b2ef6ee3efdfcbb104f3578b73a813`.
The independent report remains byte-for-byte unchanged.

Repository/branch: canonical repository only, `fix/post-a9-hardening`, existing
PR #11. Included main: `f5001b54978dc77c40468ddd099c6cbd78d86ce6`, via normal
merge `41b81c2a1a833e137a0de4ad6915bb4dedba3365`. No rebase or force push.
Follow-up implementation commit: `a036183d163eb0da3a6404477cff48a0e0616133`.

### R1–R8 disposition

| Item | Disposition | Root cause and production correction | Regression evidence / remaining limitation |
|---|---|---|---|
| R1 | FIXED | Whole-answer rejection confused suggestion safety/format with usable analysis. `filter_log_suggestions` now preserves analysis, suppresses only unsupported/unsafe NEXT_COMMANDS with visible markers and a count-only stderr warning; usable answers return rc=0. SENSITIVE_READ and UNKNOWN remain untrusted suggestions rather than automatic suppression. | Four sanitized multi-section fixtures reproduce reported gateway patterns; CLI preserves analysis and calls complete exactly once under execution traps. Accept inline none, arbitrary numbered labels, backticks with parentheses, uppercase angle placeholders, fences and trailing prose. Maximum three detected suggestions; bounded UTF-8 transport violations still fail. |
| R2 | FIXED | None/closed stdin failed during probing. Treat it as no conflicting stdin when explicit input exists. | Direct/file tests plus real `ask q <&-` subprocess returns rc=0 with one mock request. Closed stdin without explicit input remains an input error. |
| R3 | FIXED | Regular files used by outer shell loops were misidentified as conflicting sources. Probe only POSIX FIFO/socket queued bytes; TTY, regular file, character fd and unsupported/non-POSIX probing preserve explicit input. | Real positional and --file shell loops against an offline mock provider, real pipe/socket/PTY and empty-writer/EOF tests. Snapshot cannot detect later arrivals or bytes already buffered elsewhere; no blocking/poll/thread/sleep. |
| R4 | FIXED (narrow literal detection) | Shared credential-display facts cover raw Secret API paths, oc Secret reads, xargs kubectl selection, env/jsonpath/template displays, connection/auth overrides, all requested display tools/names, secret-store reads and git config --list. Gateway-exposed pg_hba.conf display adds conservative .conf handling. | All demonstrated bypasses, 13 display tools × 7 sensitive names, .conf regression, higher-risk preservation and sanitized CLI suppression. No complete shell interpretation, aliases/dynamic expressions or claim of exhaustive secret detection. |
| R5 | FIXED | Blanket isprintable rejected ordinary Unicode Cf. General text checks now reject C0 except LF/CR/tab, DEL/C1 and explicit bidi controls; ordinary ZWJ/ZWNJ/ZWSP/BOM/soft hyphen survive. Strict Cf/control checks apply only to suggestion command text. | ContextPacket/FeedbackCase roundtrips, joined emoji and unsafe-control/surrogate rejection; analysis with ANSI/emoji survives. Existing field byte caps and terminal display boundary remain. |
| R6 | FIXED | Guard imported private validator helpers and duplicated exposure rules. Public `sensitive_path`, `secret_resource`, `connection_override`, `credential_display` supply shared detection facts. A6 retains independent risk/approval policy, including higher-risk precedence. | Shared Secret template/od rules and M1 regression suite. Config-name display is conservatively SENSITIVE_READ instead of UNKNOWN; env command wrappers remain UNKNOWN in A6 and are withheld by the suggestion guard. No validator redesign or SAFE_READ expansion. |
| R7 | FIXED | Full stdin read preceded readiness. CLI now validates combinations/nonblocking explicit-input conflict before config, then readiness, actual source read, credential lookup and provider call. | Both mock read trap and real never-EOF pipe subprocess fail promptly for unconfigured profile; queued explicit-input conflict still prevents config/key/client. |
| R8 | FIXED | Current docs described destructive filtering and unsupported stdin behavior incompletely. README now matches preservation/suppression, diagnostic risk distinctions, pipe/socket snapshots, regular-file loops, closed stdin and smoke evidence. | README links this round; historical reports unchanged. M6 DEFERRED; A10 NOT STARTED. |

### Newly measured offline validation

All commands launched from the canonical repository. Canonical providers remain
offline mocks; host permission is required only for their loopback servers and
inherited provider runtime isolation. Initial sandbox socket denial was rerun with
host permission. An intermediate suite exposed two A6 expectations: config-name
display now intentionally upgrades to SENSITIVE_READ, while env wrapper detection
was corrected to retain UNKNOWN. No tests were weakened to permit execution.

| Check | Python | Discovered | Passed | Failed | Errors | Skipped | Suite elapsed |
|---|---|---:|---:|---:|---:|---:|---:|
| Focused follow-up + inherited hardening + real CLI fd cases | 3.13.3 | 34 | 34 | 0 | 0 | 0 | 1.727 s |
| Affected shared tests | 3.13.3 | 433 | 433 | 0 | 0 | 0 | 35.722 s |
| Full canonical syntax + suite | 3.13.3 | 549 | 548 | 0 | 0 | 1 | 51.469 s |
| Full canonical syntax + suite | actual 3.9.6 | 549 | 548 | 0 | 0 | 1 | 75.818 s |
| Installed final-wheel focused regressions | 3.13.3 | 32 | 32 | 0 | 0 | 0 | 0.247 s |

The inherited A5 non-UTF-8 filename filesystem skip is the sole skip. This round
adds 11 test methods plus two real subprocess CLI regressions; 536 was not reused
as final evidence. Affected shared modules are the 16 listed in the first round;
test_cli now also checks real closed-fd/regular-file loops and an open infinite pipe.
Static fixtures intentionally contain sanitized reported patterns, not private logs.

Commands/artifacts:

```sh
tools/llmtool/.venv/bin/python /private/tmp/llmtool-review-tests.py focused
tools/llmtool/.venv/bin/python /private/tmp/llmtool-review-tests.py shared
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
tools/llmtool/.venv/bin/python -m pip wheel --no-deps --wheel-dir /private/tmp/llmtool-review-wheel tools/llmtool
tools/llmtool/.venv/bin/python /private/tmp/llmtool-review-wheel-check.py
tools/llmtool/.venv/bin/python /private/tmp/llmtool-review-tests.py installed
git diff --check
```

### Final wheel

PASS: standalone wheel, SHA256
`33c97d35b59e800ef0da5304cb85e1dfba3fa8d57a6ae5dc42a8d5e2b3835e36`.
All 32 production/resources match canonical bytes (29 Python modules, 3 runtime
skills); 27 match the pre-follow-up merge HEAD, five intentionally changed:
cli, inputs, log_safety, terminal, validator. A0–A9 resources remain present;
runtime skills were not changed in round two. Tests, regression fixtures and
repository-only example plans are excluded. Installed imports are asserted under
`/private/tmp/llmtool-review-installed/llmtool`, not the source checkout.

### Required isolated company gateway smoke

Exactly four credential-free requests, using the reviewed company gateway setup
(`gw`, enable_thinking=false), isolated temporary HOME/config and an empty runtime
override environment. Real user config, credentials and project config were not
read. No personal Claude/Codex/OpenAI call, login/VPN change, retry or fallback.
Every case constructed one provider, called complete once and hit zero execution
traps (subprocess.run, subprocess.Popen, os.system). All preserved ROOT_CAUSE and
EVIDENCE with nonempty stdout; safe diagnostics remained visible.

| Synthetic case | rc | Wall time | stdout bytes | Live suppressed suggestions |
|---|---:|---:|---:|---:|
| Database authentication failure | 0 | 1.440 s | 1064 | 1 |
| OOM | 0 | 1.628 s | 1260 | 0 |
| Kubernetes BackOff / missing Secret | 0 | 1.173 s | 895 | 2 |
| nginx upstream refusal | 0 | 1.579 s | 1103 | 0 |

Transport/analysis preservation: PASS. This is not a model-quality PASS.
The database answer also exposed a previously unrecognized pg_hba.conf display.
That demonstrated gap was fixed afterward with the .conf rule and focused test.
Final filtering was replayed offline against the four retained live outputs:
database suppressed one additional suggestion, other cases zero additional; all
analysis prefixes remained identical. No extra model request was made to chase
preferred output. The final .conf/section-boundary refinements therefore have
offline replay/regression evidence, not a second live run claim.
Synthetic runtime artifacts: `/private/tmp/llmtool-review-gateway-wn961l4n`.

### Final security / Git checkpoint

Complete changed production/tests/docs reviewed. No new production subprocess,
shell=True, os.system, eval/exec, model-driven Git/filesystem mutation, native tools,
credential logging, raw stderr, process environment mutation, retry/fallback,
provider substitution, unbounded accumulation or A10/A11/A12 execution code.
Suppression is a display transformation only; no suggested command is executed.
Ordinary analysis stays under the inherited terminal boundary, and general Unicode
acceptance does not loosen A6 command classification. Readiness-before-credentials
and deterministic orchestration profiles remain covered by shared tests.

All six known unrelated untracked benchmark paths were preserved without editing,
staging, reading, moving or deleting them. The remaining scratchpad/main-docs
worktree was untouched; no new worktree/clone/alternate checkout or further
worktree diagnostic/cleanup was performed after the user's finished-cleanup checkpoint.

PR #11 is updated by normal branch push only; no gh command, duplicate PR, merge or
CI-status claim was made for this new head. Final-head GitHub CI and external
ChatGPT review remain for the reviewer to inspect. M6 remains DEFERRED; historical
Claude A8 live safety remains FAIL as measured; Anthropic A2.3 remains future.
A0–A9 DONE and merged; Post-A9 follow-up unmerged; A10 NOT STARTED.
