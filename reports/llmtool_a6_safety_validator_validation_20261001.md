# llmtool A6 — Safety & Validator Layer validation (2026-10-01)

## Scope and provenance

Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a6-safety-validator`, starting exactly at fetched origin/main
`0db76b551f62ee325ddfa9d4e2a60a3407885328`. A5 implementation and DONE/merged
Roadmap status were verified before branch preparation. A5 merge is `7de6a99`.
No worktree, clone, project copy, alternate checkout, or project relocation.
Unrelated untracked benchmark/reasoning reports are preserved and excluded.

A6 implements deterministic classification and ContextPacket validation only.
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator.
READ-ONLY != SAFE-TO-DISPLAY. A6 is not merged; external ChatGPT review PASS.
There is no executor, approval UI, trainer, agent, routing, Telegram, automatic
retrieval, repair loop, or production workflow. A7 has not started.

## Architecture and classifier

`llmtool/validator.py` is reusable pure text/data code. Frozen CommandAssessment,
ValidationFinding, ValidationResult models use CommandRisk and Severity enums;
result containers become detached tuples. JSON uses stable field order and arrays.
Severity is INFO/WARNING/ERROR; valid is derived from absence of ERROR findings.

Five risks:

- SAFE_READ: narrow recognized read/metadata forms (pwd, ls -l, stat, git status,
  git diff --stat/name-only, git log --oneline, git show --stat, kubectl get pods/events).
- SENSITIVE_READ: potential secret output. Exact regression:
  `cat /etc/company/app/config.yaml` -> SENSITIVE_READ. Metadata alternatives
  `ls -l /etc/company/app/config.yaml` and `stat /etc/company/app/config.yaml`
  -> SAFE_READ. Other cases cover .env/globs, credential/secret/token names,
  ~/.ssh/id_rsa, ~/.aws/credentials, ~/.kube/config, private key suffixes,
  credential stores/keychains, application configuration, env/printenv,
  kubectl get secret YAML/JSON, aws configure export-credentials. Raw Git diffs,
  full Git logs, pod output/describe, service status, and all ps forms are conservative
  sensitive reads because content can contain secrets.
- MUTATING: touch/mkdir/cp/mv/chmod/chown, Git mutation verbs including fetch,
  kubectl apply/patch/edit/scale/rollout restart, service operations, terraform
  apply and ansible-playbook.
- DESTRUCTIVE: rm, truncate, reset --hard, clean -f/fd, branch -D, kubectl delete,
  terraform destroy, SQL DROP TABLE/DATABASE, formatting, shutdown/reboot,
  explicit kill -9 target.
- UNKNOWN: unsupported/ambiguous commands, arguments or shell syntax. Never
  downgraded to SAFE_READ. Broad dumps/searches are not assumed safe.

Command input is bounded to 8 KiB UTF-8; nonprintable controls are rejected except
tabs. Original text is preserved in API results. Unquoted pipe/&&/||/semicolon
separators are split without evaluation; quoted separators remain argument data.
Risk floors: DESTRUCTIVE > MUTATING > SENSITIVE_READ > UNKNOWN > SAFE_READ.
Unknown components cannot make a compound safe. Redirects > and >> imply mutation;
< has sensitive classification for recognized secret targets, otherwise UNKNOWN.
Substitutions/variables/backticks/complex syntax and unsupported options remain
UNKNOWN in that component. Known risky components remain the overall minimum risk;
this is not a complete analysis of unknown components. No expansion, alias lookup,
filesystem inspection, shell config read, or full interpreter is implemented.
Conventional absolute binary paths and plain sudo command forms are recognized;
other wrappers/global-option forms remain unsupported.

requires_approval is a separate future-policy helper: false only for SAFE_READ,
true for all other/unknown values. Classification never grants execution permission
and the helper is not connected to any executor or approval engine.

## Context validation and support boundary

A4 schema, UTF-8, 1 MiB and nesting limits are reused. A6 additionally caps total
packet items at 1024 before processing, and bounds content before serialization.
Checks produce deterministic static findings with category/index locators:

- ERROR EVIDENCE_MISSING: facts/inferences exist but evidence is empty.
- ERROR INFERENCE_SUPPORT_MISSING: inference with neither fact nor evidence.
- ERROR CATEGORY_COLLISION: exact string overlaps across facts/inferences/
  hypotheses/unknowns. No case/whitespace normalization or paraphrase equivalence.
- WARNING VERIFY_NEXT_MISSING: unknowns without next evidence checks.
- WARNING VERIFY_NEXT_<risk>: each non-SAFE_READ suggestion. Ordinary prose,
  unsupported syntax and suggestions beyond classifier bounds remain UNKNOWN.

assess_verify_next preserves every original entry. A6 never promotes a hypothesis,
merges categories, invents provenance, collects evidence, or follows suggestions.
STRUCTURAL SUPPORT != FACTUAL TRUTH. Evidence presence cannot prove individual
claim support or source accuracy. VALID only means no ERROR findings; dangerous
suggestions may still be warnings, and evidence/claims can still be false.
A5 GitSnapshot consistency validation is deliberately omitted from this MVP.
No Git is rerun to validate supplied data. A4/A5/providers/chat were not redesigned.

## CLI and error contract

```sh
llmtool validate command 'cat /etc/company/app/config.yaml'
llmtool validate command 'rm -rf /' --json
llmtool validate context --file packet.json
llmtool validate context --file packet.json --json
```

Explicit offline branches precede config, skill, credential and provider resolution.
Command human output is Risk/Reason; JSON is risk/reason without echoing supplied
command text. Exit 0 means successful assessment, not SAFE_READ. Context human
output starts VALID/INVALID and prints severity/code/message; JSON contains valid,
findings and original-text command_assessments. Semantic ERROR yields exit 1 with
findings on stdout. Warnings alone yield exit 0. Malformed/oversized/file failures
yield exit 1, empty stdout, sanitized stderr; parser argument errors exit 2.
Only the explicitly selected regular packet file is read, with bounded bytes and
nonblocking open to reject FIFOs. No real user config is modified.
Existing ask/chat/context-pack/git are not automatically validated or changed.

## Validation results

All checks launched from the canonical repository using its Python 3.13 virtualenv.

| Check | Exact result | Exit | Elapsed |
|---|---|---:|---:|
| Targeted A6 | 40 tests passed, no skips/failures/errors | 0 | 0.279 s |
| Canonical syntax + suite (approved) | 345 discovered; 344 passed, 1 existing A5 skip, no failures/errors; syntax passed | 0 | 39.507 s |
| git diff --check | clean | 0 | not recorded |
| Wheel build + contents/3.9 syntax | PASS | 0 | not recorded |
| Manual offline CLI human/JSON/error | PASS | 0 | 0.032 s |

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_validator.py -v
git diff --check
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
tools/llmtool/.venv/bin/python -m pip wheel --no-deps tools/llmtool --wheel-dir /private/tmp/llmtool-a6-wheel
```

Table-driven cases cover all five classes, historical sensitive reads, compounds,
pipes, redirects, substitutions, quotes, Unicode, blank input, size/control/type
bounds, unknown syntax, risk floors, policy separation, immutable results and no
execution. Context tests cover support absence, exact/different-wording category
checks, warning-only unknowns, risky suggestions, stable order/JSON, preservation,
size/item bounds, empty packet validity, and the structural-vs-truth boundary.
CLI tests cover both formats, valid/invalid packets, duplicate/malformed JSON,
binary/oversized/missing/directory/FIFO input, sanitized errors, offline provider/
config/key/Git/network/subprocess rejection, and existing ask/context-pack behavior.
Existing full-suite ask/chat/context-pack/Git regression tests remain unchanged.

Initial targeted run: 40 tests, one failing sensitive input-redirection case.
The root cause was a leading space in the raw target; using safe tokenization for
redirect targets fixed it. Tests were not weakened. Review also added arbitrary
absolute-path rejection cases and tightened process/log reads to sensitive unless
Git log uses the narrow --oneline form before final validation.

Initial sandbox canonical attempt was not PASS: existing loopback HTTP test classes
could not bind, 304 tests ran, 2 setup errors, 1 skip, make exit 2, 26.013 s.
Approved rerun above resolves those errors. The sole remaining skip is inherited
A5 non-UTF-8 filename creation, unsupported by this macOS filesystem; A5 parser
byte preservation remains covered. No A6 test is skipped.
Sandbox wheel build failed on DNS while obtaining the existing setuptools build
dependency (exit 1). Approved isolated build passed without changing dependencies
or pyproject. Wheel includes exact validator.py/cli.py sources, all three runtime
SKILL.md resources and CLI entry point. All packaged Python parses as Python 3.9
syntax; this is syntax verification, not a separate Python 3.9 runtime test.
Wheel SHA256: `3b5b67328df763edeb25c8d9ddc746b2c8f9ea3a14ea07a8a61b66c044ecbb19`.
No live backend test is required or attempted. A6 runtime/tests are offline.

## Manual and security review

A temporary supplied incident packet included an observed HTTP 500, supplied log
provenance, inference, separate configuration hypothesis, missing previous exception,
and both cat/config and stat/config suggestions. Human/JSON validation returned
VALID with a SENSITIVE_READ warning for cat, SAFE_READ assessment for stat, and
preserved original suggestions. Command human output identified SENSITIVE_READ;
command JSON identified DESTRUCTIVE for rm. Malformed input returned exit 1 with
empty stdout and no raw private input in stderr. Temporary data was removed.

Production validator source was reviewed for subprocess, os.system, eval, exec,
shell=True and accidental Git/kubectl/terraform invocation: no execution path or
such execution imports/calls exists. AST regression and mocks reject process,
shell and socket use while assessing hostile strings. Marker-file checks confirm
suggestions have no effects. Recognized tool verbs are text rules only.
No automatic validation, file edits, command engine, approval UI or model routing.
Only six A6 files belong to this change: validator.py, cli.py, test_validator.py,
README.md, ROADMAP.md and this report.

## Limitations and disposition

Classification is conservative syntax/risk detection, not a complete shell security
policy. UNKNOWN components can have risks beyond the known floor. Command aliases,
custom scripts, wrapper/global-option forms, dynamic paths and semantics are not
resolved. SAFE_READ does not guarantee a hostile executable/environment or benign
metadata. User-supplied text in API/Context JSON is preserved, not secret-redacted.
Structural support only checks presence/collisions, not per-claim linkage, factual
truth, logical equivalence, or evidence authenticity. Warnings do not invalidate a
packet or permit execution. A5 snapshot validation is not included.
A7/A8 may consume findings for feedback/regressions; future A9/A11 must separately
provide permissions/approval enforcement. No such workflow is introduced here.

README and ROADMAP document exact behavior, examples, bounds and validation counts;
A5 was already marked merged, so its historical verification record is preserved.
External ChatGPT review: **PASS**. No blocking correctness, security, packaging, or regression issue was found in the A6 diff. No implementation or test files were changed by this review. Do not start A7 before A6 is merged.
