# llmtool A3 Skill Framework validation — 2026-09-30

## Status and provenance

A3 implementation, canonical validation, and external ChatGPT review are complete on `feat/a3-skill-framework`. Not merged to main. No A4/A5/A6/A9 work or
orchestration was added. Provider != Role != Agent != Skill.

Canonical repository and launch directory for all validation:
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Starting HEAD and locally inspected origin/main:
`ad97b8dc09b386713f665a6b053fe170724a85ee`.
Tests validate the A3 working tree based on that revision; the resulting commit
contains this report. Existing untracked benchmark/reasoning reports are preserved
and excluded. No alternate checkout, worktree, or clone was created.

## Architecture and scope

- Immutable typed `Skill`: name, description, positive integer version, instructions.
- Runtime resources: `tools/llmtool/llmtool/skills/<directory>/SKILL.md`, packaged with
  setuptools package data. Root `skills/*/SKILL.md` are repository engineering
  workflows, not runtime resources, and are never loaded.
- Explicit fixed package-local trusted root; no cwd/global discovery or configurable
  CLI resource roots. Discovery sorts declared names and validates every resource.
- Exact lookup, strict YAML frontmatter, deterministic offline CLI output, common
  provider-independent message composition. No provider implementation was changed.
- Three samples: analyze-log, review-diff, context-pack. Existing task commands
  now load those resources rather than hard-coded task bodies. Generic ask/base
  instructions remain in code.

## Schema and CLI

```yaml
---
name: analyze-log
version: 1
description: Analyze supplied logs and identify supported causes.
---
Markdown instruction body.
```

Exactly three metadata fields required. Names and directory names match
`[a-z0-9][a-z0-9-]{0,63}`. Versions must be positive YAML integers, excluding
booleans/strings. Description/body must be nonempty text without unsafe control
characters. Strict SafeLoader-based parsing rejects extra fields, duplicate keys,
aliases and unsafe tags. Duplicate declared names, invalid names/traversal,
symlink roots/entries/files, nonregular resources, invalid UTF-8, and resources
over 64 KiB fail safely. Errors omit arbitrary filenames, input, and YAML values.

```sh
llmtool skills
llmtool skills --json
llmtool skill show analyze-log
llmtool ask --skill analyze-log "supplied evidence"
```

Listing/show bypass config, credential resolution, and providers. Show prints
metadata JSON and body. `ask` never selects a skill implicitly; `--skill` does
not change saved config. Existing task commands select their corresponding
resources explicitly by command design.

Composition: a system message holds common base rules → task instructions →
labeled selected skill instructions (when requested). A separate user message
preserves text exactly. All providers receive the existing role/content mapping
contract, before dispatch. No provider-to-skill mapping or roles were introduced.

## Validation evidence

Targeted command, launched from canonical root:

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_skills.py -v
```

**PASS: 18 tests, 0.225s, exit status 0.** Covers deterministic discovery,
valid/malformed parsing, required fields, names, versions, duplicates, unknown
skills, traversal, unsupported fields, symlinks, invalid bytes/size/types,
empty/control text, deterministic text/JSON CLI listing, offline show,
legacy resource use, exact user preservation, common provider messages,
config preservation, and model-returned commands remaining text.

Canonical command, launched from canonical root:

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

**PASS: compileall syntax check and 207 tests, 25.189s, exit status 0.**
Includes the existing 189 tests plus 18 A3 tests. No tests were removed or weakened.
Loopback mock transports and fake CLI runtimes passed in this environment.
No live provider inference was performed or claimed.

Manual offline checks using the canonical editable installation:
`tools/llmtool/.venv/bin/llmtool skills --json` and
`tools/llmtool/.venv/bin/llmtool skill show review-diff`: PASS, exit status 0.
`git diff --check`: PASS, exit status 0.

Packaging check:

```sh
tools/llmtool/.venv/bin/python -m pip wheel --no-deps tools/llmtool --wheel-dir /private/tmp/llmtool-a3-wheel
```

**PASS, exit status 0 after elevated approval.** ZIP inspection confirmed the
loader and all three SKILL.md resources, byte-for-byte identical to canonical
source. The wheel is a build artifact, not an alternate checkout. Initial
`--no-build-isolation` attempt failed (exit 2: local setuptools absent); normal
sandbox build isolation failed (exit 1: dependency DNS unavailable). Approved
unsandboxed retry downloaded isolated build dependencies and succeeded. No
remaining packaging environment limitation; no wheel-installed runtime test claimed.

## Security review and limitations

Source/diff review found no added shell/Git execution, network transport, filesystem
mutation, dynamic imports from skill directories, eval/exec, executable hooks,
or automatic tool use. Schema rejects command, executable, hook, pre_run,
post_run, shell, git, tools, and all other unsupported metadata. Tests block
subprocess.run/Popen/os.system while returned shell/Git command text is printed.
Config bytes remain unchanged. Existing provider runtime boundaries remain intact.

Skills are trusted declarative instructions, not authorization or executable
workflows. Prompt rules are not an output validator; models may ignore them.
READ-ONLY != SAFE-TO-DISPLAY. Samples warn against revealing credentials/private
configuration. Installed resources must be trusted and stable while read; this
is not a hostile concurrent filesystem defense. One skill per ask; no skill config,
global plugins, version negotiation, retrieval, output validation, or orchestration.
Malformed resources invalidate discovery and exact lookup rather than being skipped.


## External review

External ChatGPT review: **PASS**. No blocking correctness, security, packaging, or regression issue was found in the A3 diff. One documentation inconsistency was corrected during review: README roadmap status for already-merged A2.5/A2.6 and A3 review state was stale. No implementation or test files were changed by this review.
