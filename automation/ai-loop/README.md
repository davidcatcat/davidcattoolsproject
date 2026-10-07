# One-command Mac development handoff

This is a development helper for David's current Codex CLI / Claude Code workflow,
independent of llmtool product features. Control files live on the persistent
`automation/ai-loop` branch; they do not need to be merged into main to be fetched.
The coordinator updates task.md/task.json on that branch for each new task.

Download scripts/ai-loop.sh once, then run:

```sh
bash ~/Downloads/ai-loop.sh
```

The launcher selects the canonical repository automatically, uses existing Git
authentication to fetch the control branch, pins its SHA, downloads its Python
runner and task files, and executes the task. There is no manual task.md copy,
Claude result copy, /import, or terminal switching. After the first invocation a
stable launcher is also available at:

```sh
bash /Users/davidsun/git/davidcataiporject/davidcattoolsproject/.git/ai-loop/run.sh
```

Run either launcher with `--check` for preflight only. It fetches Git refs and
checks the contract and dependencies, without calling either model or editing
source. Existing installations/logins for git, codex, claude, gh, make, Python 3.9+
and the project's `.venv/bin/python` are required. No API key purchase or new model
configuration is added; the CLIs use existing local authentication/model settings.
If authentication, quota, Git writes, tests or CI are blocked, the script stops
instead of bypassing sandbox restrictions or inventing PASS.

Flow: fetch pinned task → check canonical root/origin/clean tracked tree/base →
create a bounded feature branch → Codex implementation → deterministic tests →
runner commit → independent Claude review → up to two fixes → curated report →
push → exact-head CI → PR → stop for final coordinator review.

Existing untracked files are preserved and fingerprinted. No other project
checkout is created. Run only one writer in the repository: stop interactive
Codex/Claude work on it while this launcher runs. This is local automation, so the
Mac must stay awake and connected; it is not a cloud service or scheduler.

Claude has only Read/Glob/Grep tools, hooks disabled, and no MCP servers. It cannot
run tests or Git; the outer runner supplies diff/test evidence. CLI exit 0 means
the provider completed, **not** that code passed review: the runner validates
structured_output, findings, blocking flags and reviewed_head. Ordinary review is
not formal security/QA sign-off. The launcher respects configured Codex workspace
sandboxing and never uses dangerous permission bypasses.

Raw prompts, model streams and test output stay in `.ai-loop/<run-id>/` (locally
excluded via .git/info/exclude). Only curated reports are committed under
reports/ai-loop; reviewers must avoid including secrets in findings. Completion
records and bootstrap files stay in `.git/ai-loop/`. Reports distinguish the
implementation SHA from the later report/publication SHA. CI is checked against
the latter, with no inference from absent or old workflow runs.

Rerunning a completed task is a no-op. An existing unfinished task branch causes
a STOP with preserved work, requiring a coordinator-issued recovery task; the
script never resets/stashes unrelated work. Timeout and Ctrl-C terminate the
active CLI process group when its leader is still live. This convenience helper
does not claim the parent-SIGKILL/descendant guarantees of the future llmtool B5
supervisor and cannot keep running when the Mac is powered off.

Official interfaces used:
- https://learn.chatgpt.com/docs/non-interactive-mode
- https://code.claude.com/docs/en/cli-reference

Validation in this authoring environment is limited to standalone syntax and
fixture checks. No configured project checkout, Mac CLI sessions, VPN or actual
paid Codex/Claude loop was available here; run --check on the actual Mac first.
