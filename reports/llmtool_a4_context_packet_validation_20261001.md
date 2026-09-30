# llmtool A4 Evidence-first Context Packet validation — 2026-10-01

## Status and provenance

A4 implementation, canonical validation, and external ChatGPT review are complete on `feat/a4-context-packet`. Not merged to main. No A5/A6/A9/A10 implementation.
Provider != Role != Agent != Skill != Context Packet.

Canonical repository and launch directory for implementation/tests/packaging/Git:
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Fetched origin, switched main, fast-forwarded, and created the branch from exact
current origin/main `021456bc25be6695510e7db585b06ca1459152c0`, the expected A3.5 merge.
Tests validate the A4 working tree based on that revision; the resulting feature
commit includes this report. No worktree, clone, alternate checkout or project
copy. Existing six untracked benchmark/reasoning report entries remain untouched
and excluded. Git preparation used approved elevated execution.

## Schema, semantics and reusable API

`llmtool/context.py` defines frozen ContextPacket/EvidenceItem dataclasses,
`parse_context_packet(text)`, `format_context_packet(packet)`, `to_dict()` and
`to_json()`. Containers normalize list/tuple input to detached tuples internally.
Public JSON uses arrays/objects and stable field order, never Python repr.

| Field | Meaning / type |
|---|---|
| facts | Directly supported observations; tuple of strings |
| evidence | Specific supporting material/provenance; tuple of EvidenceItem |
| inferences | Supported conclusions not directly observed; tuple of strings |
| hypotheses | Possible explanations requiring confirmation; tuple of strings |
| unknowns | Important missing information; tuple of strings |
| verify_next | Specific next evidence to collect/check; tuple of strings |

EvidenceItem requires statement/source nonempty strings; locator is optional text
or None. Serialized locator is always present, null when omitted. Source examples:
supplied log, supplied diff, user-provided incident notes. Locator examples: line
numbers, resource identifiers, diff hunks. No locator or source is fabricated.
This is recorded provenance, not an external verification claim.

All six JSON fields are required arrays, including empty arrays when unsupported.
Evidence objects allow statement/source and optional locator only. Text, duplicate
claims and category membership are preserved; no deduplication, rewording,
confidence estimation, category merging or hypothesis promotion. Shape validation
cannot establish whether a purported fact is supported by supplied evidence.
Future A6 semantic validation remains separate.

## Parser, skill and CLI behavior

Strict JSON parsing is bounded to 1 MiB UTF-8 before decoding and depth 4 before
recursive parsing. Rejects missing/extra fields, non-array categories, non-string
or blank entries, malformed evidence, duplicate object keys, malformed JSON,
code fences/trailing prose, NaN/Infinity, unsafe control characters, invalid UTF-8/
surrogate text, excessive nesting and oversized output. Errors are fixed/sanitized,
without arbitrary keys, raw model output, provider metadata or stack traces.

Context-pack skill version 2 specifies the exact schema, direct evidence only,
provenance, separate inferences/hypotheses, retained unknowns, concrete verify_next,
no fabricated evidence, no hidden reasoning and no execution/retrieval.

Input → common prompt messages → existing provider → final Reply.content → strict
parser → immutable packet → deterministic renderer. Provider code is unchanged.
`llmtool context-pack` defaults to FACTS/EVIDENCE/INFERENCES/HYPOTHESES/UNKNOWNS/
VERIFY_NEXT text with source/locator; empty categories show `(none supplied)`.
`llmtool context-pack --json` emits stable JSON. Malformed output exits 1 with
empty stdout and sanitized stderr, including when verbose was requested.
Old OBSERVATIONS/UNKNOWN/NEXT_EVIDENCE_TO_COLLECT prose output is intentionally
replaced; consumers must migrate. Existing input/provider options remain.

Ask remains plain one-shot text; default chat stays unchanged, with no /context,
automatic packet extraction or history rewriting. Explicit ask/chat selection of
the context-pack skill influences the prompt only; only the context-pack CLI
command enforces packet parsing. Future A5 may collect Git evidence, A6 may check
support/safety, and A9/A10 may reuse this data model. A4 implements none of those.

## Automated validation

Targeted command from canonical root:

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_context.py -v
```

**PASS: 29 tests in 0.141s, exit status 0.** Covers construction/immutability,
detached containers, deterministic JSON/text, parsing, missing/extra/wrong fields,
non-string/blank items, malformed evidence/JSON, optional locator, output bounds,
duplicate keys, nesting/escaped delimiters, exact Unicode/text/duplicate claims,
distinct categories, provenance/unknowns/verify_next, empty categories, strict
constructor types, version-2 skill, CLI JSON/human/error behavior, no execution,
and unchanged ask/default chat.

Existing CLI stdin/unicode test now uses a valid model JSON response for
context-pack and checks its rendered output. Original transport/input assertions
remain. Other commands still use plain text. No existing test removed/weakened.

Canonical command from canonical root:

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

Initial sandbox attempt: syntax passed; 225 tests ran in 11.750s, with 2 existing
setUpClass errors (CLITests/OpenAIProviderTests) from denied loopback socket binds.
Make exit status 2. **Not PASS**; those test classes could not execute.

Approved elevated rerun of the unchanged command:
**PASS: compileall syntax and all 266 tests in 27.831s, exit status 0.**
Existing 237 plus 29 A4 tests. Mock/fake providers only, no live backend required.
No remaining canonical environment limitation after approval.

`git diff --check`, staged whitespace check, report links and Markdown fences:
PASS. Documentation updated with schema/semantics/compatibility and corrected
stale A3.5 status to merged (`021456b`); historical validation counts retained.
A4 status explicitly unmerged and awaiting external review.

## Manual functional and packaging validation

Canonical CLI main was manually exercised with an in-memory fake provider,
explicit temporary test config (no real user config read/written), and incident:
`2026-10-01T10:00Z api-7 returned HTTP 500; startup config unavailable.`

- `llmtool context-pack --json`: PASS, exit 0; valid six-field JSON.
- `llmtool context-pack`: PASS, exit 0; six labeled human sections.
- Fact api-7 HTTP 500 visible; evidence source `supplied log` and locator `line 1`
  preserved; startup-configuration hypothesis separate; previous-container
  exception remains unknown; inspect-previous-logs remains verify_next text.
- Malformed fake response: PASS of failure contract, exit 1, stdout empty;
  stderr `llmtool: context packet output is malformed JSON`; raw response absent.

This demonstrates fake-provider CLI behavior, not live inference or external review.
Live smoke: NOT-TESTED. No paid API usage, credential inspection, company VPN or
real runtime/backend access was attempted.

Packaging command with approved elevated build dependencies:

```sh
tools/llmtool/.venv/bin/python -m pip wheel --no-deps tools/llmtool --wheel-dir /private/tmp/llmtool-a4-wheel
```

PASS, exit 0. Wheel ZIP inspection confirms new context.py, updated cli.py, and
version-2 context-pack SKILL.md exactly match canonical source bytes. Existing
package discovery/data config suffices; no dependency/layout configuration change.
Temporary wheel/log files are validation artifacts, not project checkouts.
Wheel-installed runtime not separately tested.

## Security review and limitations

Complete source/diff review found no material issue. Packet code contains no
shell/Git execution, network, file discovery/reads/writes, dynamic code loading,
tools, eval/exec, output-action parsing or autonomous evidence collection.
Tests block subprocess.run/Popen/os.system while verify_next command-like text is
serialized/displayed only. Existing explicit --file reads and provider transport
boundaries remain. No hidden reasoning/provider-metadata fields are serialized.
Sensitive text explicitly present in supported fields is not automatically redacted.

Limits: structural validation only; no truth/source/support verification, fact to
evidence reference graph, safety classification or output repair/retry. Models can
mislabel/fabricate claims despite prompt instructions; the parser does not certify
their semantics. No automatic provenance inference, retrieval, command execution,
chat extraction, confidence scoring or orchestration. Output must fit bounds;
model prose/fences fail without fallback. Human rendering preserves supplied text
including multiline content and is not the machine interchange format.
External ChatGPT review: **PASS**. No blocking correctness, security, packaging, or regression issue was found in the A4 diff. Branch was observed behind `main` by one unrelated nlf roadmap documentation commit (`3d296a7`); synchronize with current `main` before merge, then re-check the combined diff. Do not start A5 before A4 is merged.
