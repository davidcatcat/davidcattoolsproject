# One-command Mac development handoff

This is a development helper for David's current Codex CLI / Claude Code workflow,
independent of llmtool product features. Control files live on the persistent
`automation/ai-loop` branch; they can be fetched without merging PR #45 into main.
The coordinator updates task.md/task.json on that branch when repo state changes.

Current task: **review and, only if necessary, repair existing PR #46**. A11b.2.2.0
is already implemented. The script does not repeat implementation, create a second
feature PR or start 2.2.1.

Download scripts/ai-loop.sh once. From its directory, run:

```sh
bash ai-loop.sh
```

The launcher automatically selects the canonical repository, fetches the control
branch with existing Git authentication, pins its SHA, downloads its Python runner
and task files, and executes the task. A previously downloaded launcher still works:
every invocation obtains the updated runner/task. No manual task.md copy, Claude
result copy, /import, or terminal switching is required. After the first invocation,
a stable launcher is also available:

```sh
bash /Users/davidsun/git/davidcataiporject/davidcattoolsproject/.git/ai-loop/run.sh
```

Add `--check` for preflight only. It fetches Git refs and checks the PR, task and
local state/dependencies without switching branches, calling models or editing
source. The normal invocation already performs preflight before paid model work;
a separate --check run is optional.

Choose local test coverage:

```sh
bash ai-loop.sh --related  # default: related local tests; full canonical CI
bash ai-loop.sh --full     # also require full local checks
```

`--test-mode related` and `--test-mode full` are equivalent alternatives. The
default is in task.json. Related commands are explicitly selected for the task,
not guessed from file names: this task covers syntax, Git-preflight, Git
intelligence/A5 and Action contracts. Each command displays its position,
PASS/FAIL and elapsed seconds; a failed command stops that phase immediately.

Full mode initially runs the canonical checks with both Mac interpreters. Each
repair runs related tests first. After review PASS, it runs final full checks if
the tested bytes changed; an initial full PASS is reused only for identical bytes.
A final full failure uses the same two-repair bound. Related mode runs no local
full suite, and explicitly reports local full checks as NOT RUN. Both modes require
the full canonical GitHub CI on the final publication HEAD, on Linux/Python 3.9
and 3.14. That CI is separate from local Mac evidence.

A running process keeps its pinned runner/task. Updates take effect at the next
launch; do not run another writer alongside it. After this task completes, a rerun
is a no-op. The coordinator must prepare a new contract for new work; changing test
mode does not rerun a completed task or bypass a moved PR HEAD.

Existing installations and usable authentication for git, codex, claude, gh, make
and Python 3.9+ are required. Preflight verifies GitHub authentication, but CLI
version/installation checks do not establish Codex or Claude login. A provider's
authentication is exercised when that provider is called; successful review skips
Codex. This task uses `tools/llmtool/.venv/bin/python` plus Mac `/usr/bin/python3`,
matching PR #46's local validation layout. Targeted unittest discovery sets
`PYTHONPATH=tools/llmtool`; canonical Makefile invocations use the correct cwd-relative
venv path. No root .venv, new API key or new model configuration is assumed.

Flow: fetch pinned task → check canonical root/origin/clean tracked tree/main/PR →
safely reuse or fast-forward the existing PR branch → deterministic tests → Claude
review → at most two Codex repairs with tests/re-review → curated report → push the
same branch → exact-publication-HEAD CI → append to existing PR description → stop
for final coordinator review. Codex is not called when the existing implementation
already passes tests/review.

The task pins an expected starting PR HEAD. Closed/merged/moved PRs, changed main,
local unpublished/divergent work, conflicting untracked files, provider/auth/quota
errors and invalid structured output stop with files preserved. Existing local PR
branches are reused; missing ones are created from the fetched PR branch, never
from main. No reset, stash, force push, alternate checkout or auto-merge occurs.

Existing untracked files are fingerprinted and preserved. Run only one writer in
the canonical repository: stop interactive Codex/Claude work on it while this
launcher runs. The Mac must remain awake, connected and logged in. This is local
automation, not a cloud service or scheduler.

Claude has only Read/Glob/Grep tools, hooks disabled, and no MCP servers. It cannot
run tests or Git; the outer runner supplies the cumulative diff and current test
results. CLI exit 0 means provider completion, not review PASS: structured_output,
blocking findings and reviewed_head are validated. Ordinary review does not replace
formal security/QA/containment gates. Codex uses workspace-write and no permission
bypass. The runner is the sole Git writer.

Raw prompts/model streams/test logs stay in `.ai-loop/<run-id>/`, excluded locally
via .git/info/exclude. Only curated summaries, test argv/exit statuses, tested file
SHA-256 and environment/version provenance are published under reports/ai-loop.
Reviewers must keep secrets out of published summaries/findings. Completion records
and bootstrap files stay in `.git/ai-loop/`. Reports distinguish the implementation
SHA from the later publication SHA. CI accepts applicable push or PR llmtool runs
on the exact latter SHA; path-filtered/missing push runs, old successful runs and
absent checks cannot establish PASS by themselves.

A completed task is a no-op on rerun. If a stopped run left local commits/edits,
the runner preserves them and requires a coordinator-issued recovery contract;
it does not overwrite them to resume. Timeout/Ctrl-C terminates the active CLI
group while its leader is still live. This helper does not claim the future B5
supervisor's parent-SIGKILL or full descendant-containment guarantees.

This current Work thread has no connected Mac executor, installed/authenticated
CLI pair, automatic callback or approval/resume service. Run the script on Mac;
results are published to PR #46 for subsequent review here.

Official interfaces:
- https://learn.chatgpt.com/docs/non-interactive-mode
- https://code.claude.com/docs/en/cli-reference

Authoring checks are standalone syntax/fixture checks. The earlier Mac run reached
Claude completion, report publication and exact-head CI waiting; the new test-mode
options have not yet been exercised on Mac. See VALIDATION.md.
