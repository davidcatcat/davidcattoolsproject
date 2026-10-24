# Mac self-hosted runner isolation (Issue #99)

The repository's CI runs on the Owner's Mac (`davidproject-self-action`,
labels `self-hosted, macOS, X64, local-mac-ci`), not on paid GitHub-hosted
runners. The runner runs as the Owner's own macOS account. Without isolation
every CI step could read SSH/AWS/VPN files, the login keychain (the `gh`
token is readable there without a prompt), other Agents' worktrees and
temp folders, and could change tools such as Homebrew's Python.

## What is installed

| File | Role |
| --- | --- |
| `runner.sb` | Sandbox profile. The runner service starts under it, so every job step inherits it whatever the workflow file says. |
| `probe.sh` | Fails unless it runs inside the sandbox. |
| `job-started.sh` | Runner hook before every job: runs `probe.sh`, then admits only this repository, the workflows listed in it, the Owner's account as actor and PR author, same-repository PRs, and pushes to `main`. |
| `profile.sh` | Prints the installed profile: `runner.sb` plus the runner folder's ancestors as single readable directories (llmtool's path checks open each ancestor). |
| `install.sh` | Copies the three files into `<runner>/isolation`, sets the runner's `HOME`/`TMPDIR` to `<runner>/_home` and `_tmp`, a minimal `PATH`, the hook, and restarts the launchd service under `sandbox-exec`. No sudo. |
| `selftest.sh` | Local check of the profile and hook against a throwaway runner folder. |

The installed copies sit in the runner folder, which the sandbox makes
read-only, so a pull request cannot weaken them. A runner started by hand
(`./run.sh`) is not sandboxed and its jobs fail at the hook.

Install or update, from a checkout of the reviewed revision, outside any
sandbox: stop a hand-started runner (Ctrl-C in its terminal), then

    bash tools/mac-runner/selftest.sh
    bash tools/mac-runner/install.sh            # default runner: ~/git/actions-runner

then run the **Mac Runner Smoke Test** workflow and check the job log shows
`runner isolation: sandboxed` and `runner admission: ...`.

## What the sandbox blocks (checked by `selftest.sh`)

- Reading anything under `/Users` (the account's home, other users,
  `/Users/Shared`), `/private/tmp`, `/private/var/tmp`, `/private/var/folders`
  and `/Volumes`, except the runner folder, the runner folder's ancestors as
  bare directories (entry names only), and folders in `/tmp`/`/var/tmp`
  named `tmp*` or `llmtool-*` (llmtool and its tests use those fixed paths).
- Writing anywhere except the runner's `_work`, `_diag`, `_home`, `_tmp` and
  those `tmp*`/`llmtool-*` folders.
- The keychain (SecurityServer), clipboard, unix sockets under those folders
  and `/private/var/run` (ssh-agent, the user's Docker socket, Agent
  servers, root service helpers such as Docker's vmnetd).
- Writing the account-wide xcrun cache in the user temp folder: the Owner's
  own `git` reads it, so a job writing it could run code outside the sandbox
  (review finding H1 on #335). Jobs may read it; `install.sh` fills it.
- Starting processes outside the sandbox: `launchctl`, `open`/LaunchServices,
  Apple Events, `osascript`.
- Terminals (`/dev/ttys*`, new pseudo-terminals): a job could otherwise read
  what the Owner types. Tests that need a pseudo-terminal fail in this CI.
- Reading another process's command line or environment.
- Terminals (`/dev/ttys*`, new pseudo-terminals): a job could otherwise read
  what the Owner types. Tests that need a pseudo-terminal fail in this CI.
- Reading another process's command line or environment.
- Inspecting or signalling the account's other processes (processes inside
  the sandbox, i.e. the runner and its jobs, may signal each other).

## What it does not block (residual risk)

- **Network.** Jobs have the Mac's network, including any VPN that is up
  (company VPN, NordVPN, AWS VPN, OpenVPN are installed; a full-tunnel VPN
  routes all traffic). Per-process network rules need root (`pf`) or a
  separate machine. This is why only trusted code is admitted.
- **The runner's own credential** (`.credentials`) is readable by jobs; a
  job could impersonate this runner to GitHub. It cannot read the Owner's
  GitHub token.
- **Same account.** This is a sandbox inside the Owner's account, not a
  separate account. The stronger fix is a dedicated non-admin macOS user
  for the runner (needs sudo, so an Owner decision), with this profile kept
  on top.
- File metadata (stat: size, times, existence) anywhere, and the entry names
  of `/tmp`, `/var/tmp` and the runner folder's ancestors; never contents.
- Loopback services of other programs (e.g. Agent servers on 127.0.0.1)
  are reachable like any network address; they must authenticate callers.
- A `tmp*` or `llmtool-*` folder another program of the account made in
  `/tmp` or `/var/tmp` is reachable. The Agents keep their data elsewhere
  (e.g. `/tmp/claude-501`, `/var/folders`), which stays blocked.
- Runner self-update cannot write the runner folder from inside the
  sandbox; update the runner by hand and rerun `install.sh`.

## Kill switch and rollback

- Stop CI without touching the Mac: set the repository variable
  `MAC_CI_ENABLED` to `false`; the job is skipped (NOT_RUN, never PASS).
- Take the runner out of service: `./svc.sh stop` in the runner folder.
- Do not "roll back" by running the runner unsandboxed, and never re-enable
  the disabled GitHub-hosted workflows (`llmtool CI`, `nlf CI`,
  `ai-loop helper CI`).
