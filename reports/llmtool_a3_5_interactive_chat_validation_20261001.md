# llmtool A3.5 Interactive Chat Shell validation — 2026-10-01

## Status and tested revision

A3.5 implementation, canonical validation, and external ChatGPT review are complete. Implemented on `feat/a3-5-interactive-chat`, not merged to main.
Provider != Role != Agent != Skill. No orchestration or A4/A5/A6/A9 work.

All work and validation launched from the canonical repository:
`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Fetched origin, fast-forwarded main, and created the branch from exact current
origin/main `c9159f2b9320e6feb51b2780339be5eaab899904` (expected A3 merge).
Tests validate the A3.5 working tree based on that commit; the resulting feature
commit contains this report. Git preparation used approved elevated execution.
No clone, worktree, alternate checkout, or project copy was created. Existing six
untracked benchmark/reasoning report entries remain unrelated and excluded.

## Architecture and commands

`llmtool chat` starts a line-oriented REPL. `ChatSession` owns a deep-copied resolved
config snapshot, selected provider profile, optional A3 skill, successful
user/assistant messages, and active state. `run_chat` handles input/display and
slash commands. Providers are unchanged and receive their existing common message
contract. A shared prompt helper composes the optional skill for both ask and chat;
existing ask composition is preserved.

Commands: `/help`, `/status`, `/provider`, `/provider <profile>`, `/skill`,
`/skill <name>`, `/skill none`, `/clear`, `/history`, `/exit`, `/quit`.
Startup and controls do not request provider inference or resolve credentials.
Status displays only profile name, provider type, skill, and individual message
count (one exchange = two turns), without endpoint/auth fields or reply metadata.

Provider switching validates the named profile and readiness offline, commits
selection only on success, and does not modify saved config or history. Retained
conversation is sent to the newly selected provider on the next user turn; the
switch prints an explicit notice. `/clear` before that next turn avoids sending
previous history. Config is resolved once at startup; startup endpoint/model/
timeout overrides stay attached to the original profile, without reapplying them
to another provider or reloading config files. Missing deployment settings allow
offline shell controls; actual requests still validate normally.

Skill selection uses the existing trusted runtime resource loader. It changes
only current system context, preserves history/config, and retains the prior skill
on an invalid selection. `/skill none` clears selection. `/clear` drops conversation
turns only, preserving provider/skill. `/history` shows user and assistant text,
not system instructions, credentials from config, hidden reasoning, or provider
stderr/metadata. It does not redact secrets explicitly supplied in conversation.

Requests deterministically contain common llmtool system rules plus the current
optional labeled skill once, followed by prior successful user/assistant messages
and exact new user input. Historical text is not rewritten during switches. A
request is detached from stored messages. A successful response commits the user
and assistant together; failures commit neither, preserve earlier turns, print a
generic sanitized error, and allow continued input/retry/switching.

One line = one message; terminal newline delimiters are removed, but spaces and
Unicode are preserved. Blank input is ignored. Leading whitespace is permitted
before slash syntax; malformed commands are sanitized and do not call providers.
EOF and Ctrl-C (including during a request) exit status 0 without traceback.
History is memory-only: no saved transcripts, database, restore, memory files,
cloud sync, or persistent shell input history. Bare llmtool retains its existing
required-subcommand behavior; automatic chat entry remains future UX.

## Automated validation

Targeted command from canonical root:

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_chat.py -v
```

**PASS: 30 tests in 0.186s, exit status 0.** Initial run had one test-harness
failure because its factory mock bypassed readiness checks; that test now uses
the real factory and validates rejection of incomplete config. No tests weakened.

Coverage: entry/exit/quit/EOF/Ctrl-C, offline safe help/status/listing, exact Unicode
and whitespace, blanks, common multi-turn messages including prior assistant text,
provider switches with notices/history/config preservation, invalid/incomplete
providers, skill selection/switch/clear/failure, clear preserving selections,
history hiding system/metadata, request rollback/recovery, sanitized raw errors,
invalid replies/input, detached requests, no output command execution or history
persistence, and unchanged ask behavior.

Canonical command from canonical root:

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

Initial sandbox attempt: syntax check succeeded; 196 tests ran in 11.484s with
2 setUpClass errors from sandbox-denied loopback socket binds in existing
CLITests/OpenAIProviderTests. Make exit status 2; **not PASS**.

Approved elevated rerun of the unchanged command:
**PASS: compileall syntax and all 237 tests in 24.647s, exit status 0.**
This is the existing 207 tests plus 30 A3.5 tests. No live model/credentials are
required. No remaining canonical environment blocker after approval.

`git diff --check` and staged whitespace check: PASS, exit status 0.
README/ROADMAP report links and Markdown fences: PASS.

## Manual UX and packaging

A real pseudo-terminal executed the canonical CLI `main(["chat"])` with an
explicit in-memory fake provider config/client. No real user configuration was
read or changed. Commands entered:

```text
/help
/status
/skill
/provider
My service is returning 500.
/history
/clear
/exit
```

**PASS, exit status 0.** Startup showed default/openai-compatible/none/0; help
listed controls; skills listed three runtime names; provider listed the fake
profile; normal input printed a fake assistant reply; history distinguished user
and assistant; clear acknowledged removal; exit ended cleanly. This is manual
fake-provider terminal UX evidence, not live inference or an external host review.

Packaging command from canonical root with approved elevated build dependencies:

```sh
tools/llmtool/.venv/bin/python -m pip wheel --no-deps tools/llmtool --wheel-dir /private/tmp/llmtool-a3-5-wheel
```

**PASS, exit status 0.** Wheel inspection verifies chat.py, cli.py, prompts.py and
all three runtime SKILL.md resources match canonical source bytes. New chat.py is
automatically included by existing package discovery; no new dependency or
packaging schema change. Wheel/logs in temporary storage are validation artifacts,
not project checkouts. Wheel-installed runtime not separately tested.

Optional live smoke: **NOT-TESTED**. No paid API usage, company VPN, credential
inspection, or real backend session was needed or attempted.

## Security review and remaining limitations

Complete source/diff review found no material issue. Chat adds no shell/Git/code
execution, filesystem mutation, model-output action parsing, automatic tools,
permissions, hooks, routing, agents, retrieval, or orchestration. Tests guard
subprocess.run/Popen/os.system while command-like model output is displayed and
stored only as text. Model-produced slash commands are not dispatched. Saved
config and temporary test files remain byte-identical; no transcript is written.
Provider errors (including unexpected exception details) are sanitized at the
request boundary, and hidden metadata is neither printed nor stored.
Existing provider transport/runtime boundaries remain unchanged. Technical
capability is not permission. A3 strict declarative skill boundaries are reused.

Limits: one line per input, no multiline editor/streaming, one optional skill,
no automatic token budgeting/truncation, unbounded in-memory history until clear
or process exit, config snapshot rather than hot reload, generic request errors,
Ctrl-C exits instead of resuming input, no output validator, and no live model
claim. The selected provider receives retained history after an explicit switch.
EOF/Ctrl-C during remote work does not establish upstream inference cancellation.
External ChatGPT review: **PASS**. No blocking correctness, security, packaging, or regression issue was found in the A3.5 diff. One stale roadmap status paragraph was corrected during review. No implementation or test files were changed by this review. Do not start A4 before A3.5 is merged to main.
