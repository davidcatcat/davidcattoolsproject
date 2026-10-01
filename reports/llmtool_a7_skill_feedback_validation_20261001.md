# llmtool A7 — Skill Trainer / Feedback Loop validation (2026-10-01)

## Scope and revision provenance

Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a7-skill-feedback-loop`, starting exactly at fetched origin/main
`4acc783fda75bb1c4eff7af0f4b9a58a824260e7`. A6 validator.py and Roadmap
`A6 — Safety & Validator Layer ✅ DONE and merged to main` were verified before
switch/pull/branch creation. A6 merge is `63b5bae`. Main and origin/main were equal;
new branch initially had 0/0 ahead/behind. Validation used this branch plus the A7
working changes in the canonical repository. Installed executable: canonical
`tools/llmtool/.venv/bin/llmtool`, version 0.1.0; Python/module paths resolve here.
No worktree, clone, alternate project checkout, project copy or relocation.
Test data, validation logs and wheel artifacts use ordinary temporary directories.
Six unrelated untracked benchmark/reasoning entries remain untouched and excluded.

A previous attempt stopped because Git reported main checked out elsewhere.
After the user released that lock, every precondition was rerun, origin fetched,
and normal main → ff-only pull → A7 branch preparation succeeded. No other checkout
was accessed or modified to bypass the lock. Git/validation/build escalation was
used where required; no host-terminal workaround or alternate checkout was used.

A7 implements a local deterministic **failure → human/external review → declarative
skill proposal → regression → manual skill revision/version bump → validation**
foundation. Skill Trainer does NOT mean gradient training, fine-tuning, RLHF, weight
updates, embeddings training or automatic prompt mutation.
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator != Feedback Case.
Implementation/validation complete on feature branch; **not merged**. External
ChatGPT review PASS. No A8/A9/A10/A11 implementation or merge is authorized.

## Architecture and models

`feedback.py` holds frozen typed data, strict parsers and supplied-output evaluation.
Skill linkage reads trusted A3 packaged resources only. `feedback_files.py` isolates
explicit file operations; `feedback_cli.py` wires five offline actions. cli.py adds
an early branch before config/key/provider resolution; ask/chat/context-pack/git/
validate behavior is unchanged. A3–A6/provider implementations were not redesigned.

FeedbackCase fields: id, skill, skill_version, input, actual_output,
expected_behavior, failure_tags, validator_findings, review_notes, status. Containers
become detached tuples; exact text is retained. Actual output may be empty for
failed/empty replies. Findings retain A6 code/severity/message/category/locator;
the API accepts explicit findings. The CLI does not infer tags or collect findings
from arbitrary prose. Tags are an explicit fixed enum:

```text
HALLUCINATION FACT_DROPPED UNSUPPORTED_CLAIM MISCLASSIFIED_FACT
MISCLASSIFIED_HYPOTHESIS MISSING_UNKNOWN MISSING_VERIFY_NEXT UNSAFE_COMMAND
SENSITIVE_READ_NOT_FLAGGED OVERLY_BROAD_COMMAND FORMAT_INVALID OTHER
```

Feedback ID is SHA-256 over canonical JSON (sort_keys=True, ensure_ascii=True,
compact separators, allow_nan=False), first 24 lowercase hex characters. Identifying
fields: skill, skill_version, input, actual_output, expected_behavior, ordered
failure_tags and ordered validator_findings. Notes/status are excluded, so review
preserves identity. Regression ID hashes all its fields except id, including ordered
source IDs/assertions. Parse verifies content matches ID. IDs are data, never paths.

FeedbackStatus progresses exactly one step through CAPTURED → REVIEWED →
PATCH_PROPOSED → REGRESSION_READY → CLOSED; nonblank review notes required after
capture. CLI review performs only CAPTURED → REVIEWED using explicitly supplied
human notes. Later statuses are API-level reviewer bookkeeping, not proof of review,
artifact existence or approval. No ChatGPT call or automatic transition occurs.

SkillPatchProposal fields: skill, from_version, proposed_version, rationale,
added_rules, changed_rules, linked_feedback_case_ids. At least one rule change and
unique nonempty links required. Proposed version must be exactly +1; same/jumps/
downgrades rejected. `propose_skill_patch` requires REVIEWED/current-version feedback.
The model/parser/API return JSON only, without applying raw patches or touching
SKILL.md. All three packaged skill resources/versions remain unchanged.

RegressionCase fields: id, source_feedback_ids, skill, minimum_skill_version,
input, assertions. Input is provenance only, not sent to a provider. Missing skill
or current version below minimum is a precondition error. Feedback version inspection
reports CURRENT, OLDER_THAN_CURRENT or NEWER_THAN_CURRENT; older historical cases
remain valid, future versions are invalid. No automatic upgrades or linked-file search.

## Validation and assertions

Strict required-only schema, enums/types, duplicate keys, UTF-8/control validation,
NaN rejection, pre-decoder depth bound and sanitized errors. Limits: JSON/file/output
1 MiB, model text 128 KiB UTF-8, collections 128 items, assertions 64, depth 6,
positive integer versions ≤ 2,147,483,647. Serialized Unicode uses JSON escapes,
so the aggregate serialization bound can be tighter than raw per-text byte bounds.

Each regression assertion requires exactly type/value/command. Fixed 12 types:
CONTEXT_VALID, FACT_CONTAINS, FACT_NOT_CONTAINS, HYPOTHESIS_CONTAINS,
HYPOTHESIS_NOT_CONTAINS, UNKNOWN_CONTAINS, VERIFY_NEXT_CONTAINS,
COMMAND_RISK_EQUALS, FINDING_PRESENT, FINDING_ABSENT, TEXT_CONTAINS, TEXT_NOT_CONTAINS.
Category assertions compare exact string elements; text assertions compare literal
substrings; finding assertions compare A6 codes. CONTEXT_VALID requires A4 parsing
and A6 validation without ERROR. No truth verification, paraphrase matching or
executable expressions. Malformed/unsupported structured output fails every structured
assertion, including negative assertions, without echoing the candidate.

COMMAND_RISK_EQUALS reuses A6. Null selector assesses the whole supplied candidate
with terminal CR/LF removed. An explicit selector must occur as an exact candidate
line or packet verify_next element; absent commands fail rather than being assessed
from the assertion alone. No suggested command ever becomes an action. Results
preserve assertion order/index/type/pass/static message, deterministic JSON and
human PASS/FAIL. Risk classification does not grant execution permission.

Curated fixtures live under `tools/llmtool/feedback/` (repository-local, not wheel
resources). Historical provenance is the [A1.1 report](llmtool_a1_1_live_smoke_20260929.md),
which recorded the model suggestion `cat /etc/company/app/config.yaml` without
executing it. Regression: cat = SENSITIVE_READ; metadata alternatives ls -l/stat
= SAFE_READ, **3/3 assertions pass**. The proposal artifact is illustrative, not
applied. This guards classification; it does not claim a live model stopped
suggesting cat or that any command was executed.

Context quality fixture is human-curated, not a fresh live-model observation.
HTTP 500 remains fact; configuration mismatch is hypothesis, absent previous
container exception remains unknown; verify_next is concrete and provenance is
supplied incident notes. **6/6 assertions pass**, including structural support.
STRUCTURAL SUPPORT != FACTUAL TRUTH remains the A6 boundary.

## CLI/file/security contract

```sh
llmtool feedback capture --skill context-pack --input-file incident.txt \
  --output-file model-output.txt --expected-file expected.txt --tag FORMAT_INVALID \
  --case-file case.json
llmtool feedback show --file case.json [--json]
llmtool feedback validate --file case.json [--json]
llmtool feedback review --file case.json --notes-file review.txt
llmtool feedback regression --case regression.json --output-file candidate.json [--json]
```

Capture/review print deterministic case JSON. Omit case-file for stdout-only capture.
No automatic writes during ask/chat/context-pack. Only explicit regular UTF-8 files
are read, bounded/nonblocking and no-follow, using anchored directory descriptors.
Symlink components and parent traversal rejected; physical paths required. New .json
files publish atomically without overwrite, 0600. Review atomically replaces only
the explicit parsed case, preserving ordinary permission bits and ID. Content and
identity/mtime checks catch intervening updates before replacement; concurrent
writers/hostile filesystem races are not a multi-user transaction guarantee.
Temporary files are removed on publication/failure; no implicit directories.

Exit 0: completed capture/review/valid version or all regression assertions passed.
Exit 1: invalid future version, failed assertion, sanitized schema/linkage/file error.
Argument errors: 2. Malformed input/precondition/file errors leave stdout empty;
regression failures return static per-assertion reports. Raw malformed contents,
paths and credentials are omitted from error messages.

Source review and AST/mock tests show no A7 subprocess, os.system, eval, exec,
shell=True, dynamic Python loading, Git command invocation, provider creation,
API key resolution or automatic patch/skill write. Cases/proposals/suggestions are
text only. Explicit capture/review are the sole filesystem write workflow; model
output never directs it. Hostile command text has no marker-file effect. No API
request, credential inspection, VPN or live backend is required or attempted.
Recognizable private-key headers, AWS access IDs, bearer/OpenAI-style tokens and
credential assignments are rejected without echoing; redacted placeholders allowed.
This is not comprehensive secret detection. Users must curate/redact material and
exclude hidden reasoning themselves; show/JSON displays explicitly recorded text.

## Exact validation results

All checks launched from the canonical repository, Python 3.13 virtualenv.

| Gate | Exact result | Exit | Elapsed |
|---|---|---:|---:|
| Final targeted A7 | 47 passed; no errors/failures/skips | 0 | 0.626 s |
| Final canonical syntax + suite (approved) | 392 discovered; 391 passed, 1 inherited A5 skip, no errors/failures; compileall passed | 0 | 39.826 s |
| git diff --check | clean | 0 | not recorded |
| Final wheel build / exact contents / Python 3.9 syntax | PASS; 3 unchanged skill resources, all new modules/entrypoint present; fixtures excluded | 0 | build not recorded |
| Manual CLI/API workflow with offline guards | capture/show/validate/review/proposal, both regressions and sanitized malformed case PASS | 0 | 0.103 s |
| Installed CLI fixture validation/regressions | CURRENT, 3/3 safety assertions, 6/6 context assertions PASS | 0 | not recorded |

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_feedback.py -v
git diff --check
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
tools/llmtool/.venv/bin/python -m pip wheel --no-deps tools/llmtool \
  --wheel-dir /private/tmp/llmtool-a7-wheel
```

First targeted run: 46 tests, two failures from incorrect test expectations: the
constructed aggregate size was below 1 MiB, and the allowlist contains 12 types,
not 13. Corrected input size/count without relaxing product validation; 46 passed
in 0.635 s. Security review added credential-assignment/redacted-placeholder coverage,
then final 47 passed. No existing test was removed or weakened.

Initial sandbox canonical was **not PASS**: 350 tests ran, two inherited loopback
HTTP class setup errors (bind denied), one A5 skip, make exit 2, 25.923 s. Approved
rerun: 391 tests/390 passed/one skip, exit 0, 39.462 s. Final rerun after security
coverage produced the 392-test result above. The inherited skip is non-UTF-8 filename
creation rejected by the host filesystem/sandbox; A5 byte parser remains covered.
No A7 test was skipped. Ask/chat/context-pack/git/validator regressions remain covered
by unchanged existing tests in the canonical suite.

Sandbox wheel attempt failed DNS while obtaining existing setuptools build dependency
(exit 1); approved isolated builds passed. No pyproject/dependency change. Final wheel
includes exact final feedback/CLI modules, unchanged skill resources and entrypoint;
all packaged Python parses as Python 3.9 syntax (not a separate 3.9 runtime test).
Final wheel SHA256: `d7f39f46d75eb2e2b74a6890a86a24a4af66487d8c3723139c94b40afb438a60`.

## Review, documentation and limitations

Full intended source/test/fixture/documentation diff reviewed before staging; no
material execution, provider/config, patch-application or compatibility issue found.
README, ROADMAP and fixture README describe schema, IDs, lifecycle, version rule,
assertions, file/credential boundaries, CLI exits, exact counts and limitations.
A6 was already DONE/merged; historical completion/validation records remain intact.
This report is part of the A7 commit; commit/push result and final HEAD are reported
after Git publication. A7 remains unmerged; external ChatGPT review PASS.

Limitations: explicit tags only; proposal/later lifecycle via reusable API, no proposal
CLI or auto-apply; no evidence truth checks, semantic matching, linked-case discovery,
live provider regression/benchmark, scoring or analytics. POSIX file adapter requires
no-follow/dir_fd support and physical paths; Windows, ownership/ACL preservation and
concurrent writers are not supported. Credential rejection is heuristic, not proof
of redaction. Cases and statuses are human-managed data, not authenticated approval.
A8 may add collection/measurement independently; A7 provides no agent/orchestration,
automatic repair, retrieval, shell/Git mutation or production execution.


## Post-hardening main synchronization validation — 2026-10-01

Historical pre-sync results above remain intact. Old A7 HEAD:
`97ab0a362ac17e3ab16c7c589169071a03b16d25`. Hardened main:
`c8c63d2803228104fdf5227fa382484a24364fec` (bugfix PR #7 merge `643caa2`).
Synchronization merge: `eefa616cf6c58deff8d9d08b7a4a9596cf778fac`.
No conflicts occurred; Git automatically combined README and cli.py. The verified
cli.py delta over main is only four additive lines: feedback import, parser and
early offline dispatch. Shared input/terminal/chat/config/provider/validator source,
shared tests and CI retain current main bytes; M1–M7 and Round-2 hardening are retained.

The 16-file A7 delta contains only the three feedback modules, minimal CLI integration,
feedback tests/fixtures and A7 docs/report. A7 production, tests and fixtures are
byte-identical to the previously externally reviewed A7 HEAD, so external ChatGPT
review PASS remains applicable. No unrelated hardening follow-up was implemented.

All validation ran from the canonical repository on synchronization HEAD plus docs:

| Gate | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
|---|---:|---:|---:|---:|---:|---:|
| A7 focused test_feedback.py | 47 | 47 | 0 | 0 | 0 | 0.665 s |
| Shared CLI/validator/context/input/terminal/chat | 145 | 145 | 0 | 0 | 0 | 14.263 s |
| Canonical Python 3.13.3 syntax + complete suite | 423 | 422 | 0 | 0 | 1 | 41.875 s |
| Actual Python 3.9.6 syntax + complete suite | 423 | 422 | 0 | 0 | 1 | 65.715 s |

Canonical: `make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"`.
Python 3.9: `make -C tools/llmtool check PYTHON=/usr/bin/python3`.
Both exited 0; one inherited A5 non-UTF-8 host-filesystem skip, no A7 skips.
Shared and complete suites used approved host execution for loopback fixtures.
`git diff --check` PASS. Current counts supersede historical 392-test evidence.

Packaging PASS: isolated wheel build; exact source bytes for feedback.py,
feedback_cli.py, feedback_files.py, cli.py, validator.py and terminal.py verified;
three packaged SKILL.md resources match main unchanged; repository fixtures excluded.
Wheel SHA256: `e0a449f6acd167248df6d1e18a0ca50e5b8a9354cca40becd23271aa969b8b83`.

Installed canonical llmtool fixture regressions: historical sensitive-read 3/3 PASS;
ContextPacket quality 6/6 PASS (HTTP 500 fact, configuration mismatch hypothesis,
missing exception unknown, verify_next present, structurally valid packet).
Eleven required hardened classifier cases PASS with approval required, using only data.

A7 security review PASS: source and AST/mock coverage confirm fixed assertion allowlist,
strict bounded immutable models, deterministic IDs/results, exact forward lifecycle,
reviewed/current skill proposal prerequisites and +1 version rule. No provider or
credential resolution, shell/command/Git execution, eval/exec, executable JSON loading,
repository scanning, automatic SKILL.md mutation or arbitrary patch application.
File reads/writes remain explicit case operations; feedback/regression content is data.
No current live-provider call was needed or performed.

README/ROADMAP now distinguish merged A0–A6 and bugfix hardening from synchronized,
validated A7. A7 NOT YET merged at this validation stage. Existing PR #6 will be
updated and marked ready after these local gates; GitHub CI result is pending push.
Deferred Git-discard severity and CI-hardening follow-ups remain deferred. A8 excluded.
