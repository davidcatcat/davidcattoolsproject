**RCA_CONFIRMED. Recommend design B: fail closed.** Stop rewriting paths inside arbitrary prose. Keep slash-free descriptions, format validated structured targets, and check the final label before display.

| Field | Required content |
| --- | --- |
| **Identity and source** | Original Task: W57 TUI UX, PR #696; verified D6 ID `tui-ux-w57-reapply-20261030`. Separate High Debug Task ID: **UNKNOWN—not supplied**. Examined SHA: **`fe8738bbf21e9277aff53e4a13cffed3f51ebe83`**. Worker: this read-only Codex diagnostic session; assigned provider/model: Codex CLI / `gpt-6.1-sol`; actual model and reasoning were not independently exposed by runtime evidence, so **UNKNOWN**. Two failed corrections in the same path-label privacy family. Scope: relevant repository source, tests, local Git history and synthetic read-only probes. Date: **2026-10-30**. |
| **Symptom and reproduction** | Confirmed structured-target bypass, punctuation-boundary leaks, overbroad URL protection, path-list corruption and placeholder collision. Exact inputs/results appear below. Repair 3 mishandled delimiters and rewrote relative paths/URLs. Repair 5 improved ordinary cases but retained separate processing branches, greedy tokens and input-collidable placeholders. |
| **Root Cause — required** | **CONFIRMED** by exact-source inspection and executable probes: the required invariant—every displayed tool label excludes absolute local paths—is enforced only on selected inputs and branches. Token recognition is incomplete, token consumption is greedy, URL protection hides paths, and placeholder restoration trusts model-controlled syntax. `_TurnParser.feed()` reaches the failing helper directly. |
| **Exactly what to modify — required** | Replace the prose-redaction block in `tools/llmtool/llmtool/pm_runtime.py:240–286` with design B below. Retain lexical repository/home/basename shortening, fix its `/` fallback at line 237, and expose a validated `display_path()`. Change `/status` at `chat_tui.py:1065–1070` to use that structured helper. Add the final display check in `chat_tui_pm.py::tool_label`, lines 233–253. Replace the obsolete redaction expectations in `test_pm_runtime.py:299–323`; extend TUI tests as specified below. |
| **Verification** | Exact-SHA read-only probes reproduced every reported v3 failure, including `IndexError` through `_TurnParser.feed()`. Repair-3 source probes reproduced all reported corruption examples. Three existing helper-test methods passed when invoked without their filesystem setup. An **in-memory proposed implementation passed 43 assertions**; this is prototype evidence, not an implemented repair. Full suites, CI and interactive acceptance: **NOT_RUN**. |
| **Risks and handoff** | Legitimate slash-containing prose and unusual structured targets lose detail and fall back to the tool name. Plain descriptions and ordinary structured filenames remain readable. Proposed disposition: **NOW; blocker for W57 acceptance**, pending PM/formal Reviewer classification. No mutation was performed. PM must separately authorize implementation, reread dispatch policy and verify the D6 single-writer transition before assigning the original Developer. This report grants no additional repair attempt. |
| **Outcome/cost** | **`RCA_CONFIRMED`**. Provider usage, token totals and cost: **UNKNOWN**. Remaining uncertainties: separate diagnostic Task ID, independently verified model/reasoning, actual repair validation and native acceptance. PM next action: disposition this bounded redesign and, if authorized, assign implementation followed by independent review. No edits, commits, pushes or posts occurred; HEAD and working tree remained unchanged. |

The source-level causes at this SHA are precise:

- [`pm_runtime.py:218`](/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/tui-ux-w57-reapply-20261030/tools/llmtool/llmtool/pm_runtime.py:218), lines 218–237: `_display_local_path()` only recognizes strings **starting with `/`**. A structured `file:/Users/...` target returns unchanged.
- Lines **269–283**: the structured fallback and description branches take different paths. Only the latter calls `_redact_description_paths()`.
- Lines **240–241**: the URL expression consumes everything until whitespace, including `file:///...` and a comma-adjacent local path. The path lookbehind permits only its enumerated preceding characters.
- Lines **241, 245–247**: the match consumes `:` inside `PATH=/a:/b` as part of one path. Passing that whole string to basename produces `b`.
- Lines **254–262**: model input can contain the placeholder syntax itself. Restoration indexes `urls[7]` without establishing that the placeholder was created by this invocation.
- Lines **670–677**: `_TurnParser.feed()` calls `_tool_description()` while constructing a `TOOL_USE` event. A synthetic valid JSON assistant frame containing `description="\x00URL7\x00"` raises **`IndexError`** there; it does not become a controlled parser error.

These were the confirmed exact-SHA results:

| Probe | Actual result | Required behavior |
| --- | --- | --- |
| `_tool_description('Read', {'path': 'file:/Users/alice/private.pem'})` | `Read file:/Users/alice/private.pem` | Safe target or tool-name fallback |
| Description `c + '/Users/alice/secret.pem'`, for each `c` in `` ` > < [ \| ; `` | Absolute path remains | No absolute path displayed |
| Description `file:///Users/alice/secret.pem` | Unchanged | No local path displayed |
| Description `https://x.com/a,/Users/alice/b` | Unchanged | No local path displayed |
| Description `PATH=/a:/b` | `PATH=b` | No misleading rewrite |
| Description `PATH=/a:/opt/private/bin` | `PATH=bin` | No misleading rewrite |
| Description `'\x00URL7\x00'` | `IndexError` | Label omitted; stream continues |

Repair-3 source at `6756c8c` also reproduced `host:/Users/x/k.pem` and `file:/Users/...` leaks, `~/notes/x.md → ~x.md`, `https://example.com/a/b → https:/b`, and `PATH=/a:/opt/private/bin → PATH=bin`.

The current [`/status` implementation](/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/tui-ux-w57-reapply-20261030/tools/llmtool/llmtool/chat_tui.py:1065) **does shorten ordinary absolute paths**: synthetic cwd `/repo` displayed `.`, and `/Users/alice/.claude` displayed `~/.claude`. The repair-3 `/status` leak is historical, not a general current failure. Current `/` still displays `/`, because line 237 explicitly preserves it.

| Design | Security | Readability | Risk of another round |
| --- | --- | --- | --- |
| **A: tokenizer** | Can satisfy a defined grammar if every branch uses it, `file://` is local, path lists split correctly and placeholders disappear. Ambiguous URL/prose boundaries still need explicit policy. | Preserves more prose, URLs and shell-like descriptions. | Higher: lexer rules, punctuation, colon contexts and all callers require substantially broader testing. |
| **B: fail closed** | Small, auditable admission rules. Ambiguous content is omitted rather than transformed. Structured inputs must also be validated. | Keeps `列出 PR 狀態`, `List files`, `Read src/app.py`, and `Edit ~/notes/x.md`. Slash-containing prose falls back to a structured target or tool name. | Lower: removes the failing parsing mechanism and supports a bounded regression matrix. |

**Choose B.** Its readability tradeoff is explicit and predictable. It preserves the Owner’s plain action descriptions whenever those descriptions contain no slash.

The exact bounded implementation is:

1. In `pm_runtime.py`, delete `_URL_TOKEN`, `_ABSOLUTE_PATH_TOKEN`, `_redact_path_match()` and `_redact_description_paths()`.
2. Change `_display_local_path()`’s final fallback to:
   ```python
   return os.path.basename(path) or '[root]'
   ```
3. Add these helpers and replace `_tool_description()` with the following logic. The conservative component grammar deliberately rejects schemes, prefixes, shell syntax, traversal components and control characters.

```python
def _is_display_path(value):
    if not isinstance(value, str) or not value:
        return False
    if value in ('.', '~', '[root]'):
        return True
    tail = value[2:] if value.startswith('~/') else value
    return all(
        part
        and part not in ('.', '..')
        and part == part.strip()
        and all(char.isalnum() or char in ' ._-' for char in part)
        for part in tail.split('/')
    )


def display_path(value, cwd=None):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value == '/':
        return '[root]'
    raw = value[1:] if value.startswith('/') else value
    if not _is_display_path(raw):
        return None
    shown = _display_local_path(value, cwd)
    return shown if _is_display_path(shown) else None


def _label_is_safe(label, name):
    if not isinstance(label, str) or not all(
            char.isprintable() for char in label):
        return False
    if '/' not in label:
        return True
    prefix = name + ' '
    return (
        '/' not in name
        and label.startswith(prefix)
        and _is_display_path(label[len(prefix):])
    )


def _tool_description(name, tool_input, cwd=None):
    if (not isinstance(tool_input, dict)
            or not isinstance(name, str)
            or '/' in name
            or not name.isprintable()):
        return None

    raw = tool_input.get('description')
    text = None
    if (isinstance(raw, str)
            and '/' not in raw
            and all(char.isprintable() or char.isspace() for char in raw)):
        text = ' '.join(raw.split())

    if not text:
        for key in _TOOL_TARGET_KEYS:
            target = display_path(tool_input.get(key), cwd)
            if target:
                text = name + ' ' + target
                break

    if not text:
        return None
    screened = _screen_stream_text(text[:TOOL_DESCRIPTION_CHARS])
    return (screened if screened != '[withheld]'
            and _label_is_safe(screened, name) else None)
```

The final check admits slashes **only inside a complete validated relative or `~/` target following the tool name**. It does not attempt another prose-token rewrite.

4. In `/status`, replace both redactor calls with `display_path()`; retain `safe_text()` so an unavailable/rejected value displays `UNKNOWN`. Pass cwd when formatting cwd; preserve home/basename semantics for the account directory.
5. In [`chat_tui_pm.py::tool_label()`](/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/tui-ux-w57-reapply-20261030/tools/llmtool/llmtool/chat_tui_pm.py:233), apply `_label_is_safe()` to the normalized, screened description before width clipping. Reject invalid labels to the tool-name fallback. Ensure that fallback itself contains no `/` or nonprintable characters; otherwise use fixed `Tool`. This covers older or independently constructed events too.
6. Preserve credential screening, the 240-character runtime bound, the 120-cell UI bound and the rule that command/tool-output fields never supply labels.

Use a standalone `ToolLabelPathTests(unittest.TestCase)` in `tools/llmtool/tests/test_pm_runtime.py`, avoiding the existing filesystem-writing setup for these pure helper tests. The exact regression matrix should be:

| Test | Inputs | Exact expected result |
| --- | --- | --- |
| `test_slash_descriptions_are_omitted` | Every description listed below, no structured target | `_tool_description('Bash', ...) is None`; rendered label `Bash` |
| `test_ambiguous_structured_targets_are_omitted` | Prefix/URL/PATH/punctuation cases below as `path` | `_tool_description('Read', ...) is None`; rendered label `Read` |
| `test_rejected_description_uses_structured_target` | Description `/Users/alice/secret.pem`, path `/repo/src/app.py`, cwd `/repo` | `Read src/app.py` |
| `test_structured_path_display` | Structured path cases below, patched home `/Users/alice` | Exact labels below |
| `test_plain_descriptions_survive` | `List  files\n`; `列出 PR 狀態` | `List files`; `列出 PR 狀態` |
| `test_placeholder_input_cannot_crash_parser` | `\x00URL7\x00`, alone and following a URL, in a JSON `tool_use` frame passed to `_TurnParser.feed()` | No exception; `TOOL_USE` description `None`; subsequent frame still parses |
| `test_final_label_guard_rejects_bypasses` | Raw `Read file:/Users/alice/private.pem`, `Read /Users/alice/private.pem`, URL-comma leak | Guard rejects; TUI displays `Read` |
| `test_commands_and_sensitive_text_stay_hidden` | Existing command-only and credential fixtures | Existing protections retained |

The complete description list is:

```python
bad_descriptions = [
    'host:/Users/x/k.pem',
    'scp host:/Users/x/k.pem .',
    'file:/Users/alice/private.pem',
    'cat ~/notes/x.md',
    'fetch https://example.com/a/b',
    'PATH=/a:/opt/private/bin',
    'export PATH=/a:/opt/private/bin',
    'PATH=/a:/b',
    'file:///Users/alice/secret.pem',
    'https://x.com/a,/Users/alice/b',
    'Read /var/private/client.pem',
    'keep ./relative/path and docs/a.md',
    '\x00URL7\x00',
    'https://example.com/a/b \x00URL7\x00',
] + [prefix + '/Users/alice/secret.pem'
     for prefix in '`><[|;']
```

For structured-target rejection, use the `host:`, `file:`, `file://`, URL-comma and both `PATH=` strings, plus **each** punctuation-prefixed path. Test each path-bearing key: `file_path`, `notebook_path`, and `path`.

The exact structured-path expectations, with cwd `/repo` and patched home `/Users/alice`, are:

```python
[
    ('/repo/src/app.py', 'Read src/app.py'),
    ('/Users/alice/private/keys/client.pem',
     'Read ~/private/keys/client.pem'),
    ('/var/private/client.pem', 'Read client.pem'),
    ('~/notes/x.md', 'Read ~/notes/x.md'),
    ('docs/a.md', 'Read docs/a.md'),
    ('/repo', 'Read .'),
    ('/', 'Read [root]'),
]
```

Replace the existing expectation that `PATH=bin` is correct. Under B, slash-containing prose is **omitted**, including legitimate URLs and relative-path prose; structured relative and home paths remain readable.

In `test_chat_tui.py`, retain `test_bash_line_says_what_it_does_never_the_command`, extend the PM tool-label test with rejected descriptions and prefixed targets, and extend `test_status_in_pm_mode_shows_the_pm_in_chinese` with cwd `.`, home account `~/.claude`, external-account basename, missing account `UNKNOWN`, and root `[root]`. Assert raw synthetic absolute paths are absent from the **status response**, excluding connection notices.

The authorized Developer should run:

```sh
python3 -B -m unittest discover -s tools/llmtool/tests -p test_pm_runtime.py
python3 -B -m unittest discover -s tools/llmtool/tests -p test_chat_tui.py
```

Then require independent Claude review of the Codex-authored repair, with PM confirming review coverage for any Claude-authored integration changes; exact-head Linux 3.14 and Owner self-hosted macOS 3.13 CI; and actual Mac TUI acceptance of readable tool rows, uninterrupted parsing and `/status`. Windows and Linux 3.9 remain **NOT_RUN** under the current PR gates.

This is a POSIX-label containment change, not a claim that all PM text or all platform path syntaxes are sanitized. Containment is the fixed tool-name fallback; reverting to the current redactor would reopen confirmed failures.

你需要做：無