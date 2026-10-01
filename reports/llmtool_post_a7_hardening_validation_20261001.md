# llmtool post-A7 hardening validation — 2026-10-01

## Scope and revision

Canonical repository only: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch `fix/llmtool-post-a7-hardening`, based on fetched main
`a73db60a50a3ebdd480cf55b019beff2b04d8510`. A0–A7 and the prior bugfix pass
remain DONE and merged. This pass addresses only destructive Git severity and
llmtool CI. A8 NOT STARTED; A7 feedback, providers, orchestration, execution,
approval policy and Telegram roadmap are unchanged. Unrelated untracked files preserved.

## Validator changes

- Default path restore (`restore .`, `restore src/a.go`) and explicit worktree
  restore (`--worktree` / `-W`) are DESTRUCTIVE, including common option ordering
  and existing `-C` / `-c` prefixes. `--staged` / `-S` alone remain MUTATING;
  requesting both index and worktree is DESTRUCTIVE. Unknown restore flags or
  missing paths return UNKNOWN; no full Git parser was introduced.
- Exact checkout force flags (`-f`, `--force`) and switch `--discard-changes`
  before the option terminator are DESTRUCTIVE. Ordinary branch checkout/switch
  remain MUTATING. Existing checkout path restoration remains DESTRUCTIVE.
- Exact stash drop/clear and numeric drop references are DESTRUCTIVE. A narrow
  literal `stash@{N}` exception is recognized only for Git stash drop; other brace
  syntax remains UNKNOWN. Stash/push/list retain existing MUTATING classifications.
- Required historical raw Git, reset/clean, push, sudo and sensitive-cat/metadata-ls
  classifications remain covered. The table helper verifies approval for every case:
  only SAFE_READ avoids approval. No command is executed by classification or these tests.

The `-W` flag and staged/worktree semantics were verified against the
[official Git restore documentation](https://git-scm.com/docs/git-restore).

## CI hardening

- checkout v6.0.2 pinned to `de0fac2e4500dabe0009e67214ff5f5447ce83dd`.
- setup-python v6.1.0 pinned to `83679a892e2d95755f2dac6acb0bfd1e9ac5d548`.
  Both official release tags were resolved through GitHub API to commit objects;
  human-readable versions are comments, not floating refs.
- `persist-credentials: false`; permissions remain only `contents: read`.
- New `tools/llmtool/requirements-ci.txt` pins PyYAML 6.0.3, the canonical
  Python 3.13 environment version. It installs before `pip install --no-deps -e`.
  Exact runtime/test dependency pin, not a hash lock; package compatibility range
  and dependencies are unchanged. No new dependency manager or package dependency.
- Final clean-tree check matches nlf's `git status --porcelain` pattern and fails
  on tracked changes or untracked files. Local unrelated untracked files are not
  removed to simulate CI cleanliness; fresh GitHub checkouts validate this gate.
- 15-minute job timeout; repository-convention concurrency cancels superseded PR runs.
- Ubuntu Python 3.9 / 3.14 matrix and llmtool/workflow path filters retained.
- No live providers, user authentication, repository secrets, API keys or VPN.

## Local validation

| Gate | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
|---|---:|---:|---:|---:|---:|---:|
| Focused validator | 50 | 50 | 0 | 0 | 0 | 0.344 s |
| Canonical Python 3.13.3 | 426 | 425 | 0 | 0 | 1 | 40.916 s |
| Actual Python 3.9.6 | 426 | 425 | 0 | 0 | 1 | 64.877 s |

Commands launched from canonical repository:

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_validator.py -v
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

Both complete suites include compileall and exited 0. One inherited A5 non-UTF-8
host-filesystem skip; no new skip. Approved host execution enabled loopback fixtures.
Local Python 3.9 uses installed PyYAML 6.0; canonical Python 3.13 uses 6.0.3.
GitHub matrix will test the exact CI pin on both versions.
YAML syntax/matrix, SHA format, permissions, timeout, credentials, path filters and
clean-tree step inspected; PASS. Documentation links and diff whitespace PASS.

## Security review and deferred items

Final source/test/workflow diff reviewed: PASS, no material finding. Validator remains
classification-only: no subprocess, shell=True, os.system, eval/exec, automatic execution,
Git mutation, provider/network call or credential logging introduced. Tests use direct
classifier calls; complete suite remains fake/loopback only. A7 behavior is unchanged.
CI adds no write permission or secret access. No A8 implementation.

Deferred: dependency artifact hash locks and build-tool lock strategy; broader shell/Git
syntax recognition. The runtime pin provides version determinism, not cryptographic
supply-chain guarantees. No unrelated historical nit is addressed.

## GitHub and merge status

Pending branch push/PR CI. Local validation is not a GitHub Actions PASS.
Normal merge is permitted only after both Python matrix jobs succeed and expected diff
scope remains intact. Final remote evidence and merge status will be appended after gating.
