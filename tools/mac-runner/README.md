# Mac self-hosted runner isolation (Issue #99)

**Status: retired (Owner [#1253](https://github.com/davidcatcat/davidcattoolsproject/issues/1253), 2026-11-09).**
This self-hosted setup is retired. Mac CI runs on GitHub-hosted
`macos-15-intel`; these runners take no CI jobs, and re-enabling them needs a
new Owner grant. This page is kept as a record.

The repository's CI runs on the Owner's Mac (`davidproject-self-action`,
labels `self-hosted, macOS, X64, local-mac-ci`), not on paid GitHub-hosted
runners. The runner runs as its own hidden, non-admin macOS account,
`ghrunner`, so CI jobs (pull request code) cannot read the Owner's files
or keychain (the `gh` token), or signal the Owner's processes or terminals.

A sandbox inside the Owner's account was tried first (PR #335, earlier
commits) and dropped: macOS cannot give the runner TLS (it needs the
keychain service) while keeping the Owner's keychain closed, and a sandbox
cannot be nested to treat jobs differently from the runner.

## What is installed (`sudo bash tools/mac-runner/install.sh`)

| Part | What it does |
| --- | --- |
| `ghrunner` account | Own group (not `staff`, not `admin`), hidden, no shell, no password. Its home is 0750 with group `admin`, so the Owner can read the runner's logs; `_work`, `_tmp` and the credential stay ghrunner-only. |
| Owner's home | `chmod o-rwx` (the macOS default 750), so other accounts cannot read it. |
| `/Users/ghrunner/actions-runner` | Copy of the configured runner. Owned by root; `ghrunner` owns only `_work`, `_tmp`, `_diag` (group `admin`, so the Owner reads logs) and its own credential. |
| `isolation/probe.sh` | Fails unless it runs as an account other than the Owner, outside `admin`/`staff`, that cannot read the Owner's home or write the runner folder, and whose packets to the LAN gateway are dropped (the pf block is in place). |
| `isolation/job-started.sh` | Runner hook before every job: runs the probe, then admits only this repository, the listed workflows, the accounts `davidcatcat` and `ddavidccatssun` as actor and PR author, same-repository PRs, and pushes to `main`. |
| pf anchor `com.apple/ghrunner` | Drops `ghrunner`'s TCP/UDP to private ranges (10/8, 172.16/12, 192.168/16, 100.64/10, 169.254/16, fc00::/7, fe80::/10): company VPN and LAN. Loaded at boot by `local.ghrunner.pf`. It matches only `ghrunner`; other traffic is unchanged. |
| LaunchDaemon | `actions.runner.…davidproject-self-action` runs the runner as `ghrunner`; the Owner's LaunchAgent for it is removed. |

`install.sh` checks each part (account not in admin/staff, `ghrunner`
cannot list the Owner's home or reach the default gateway, can reach
GitHub, probe passes) before it starts the service. `selftest.sh` (no sudo)
checks the probe and the admission rules.

After installing, run the **Mac Runner Smoke Test** workflow and check the
job log shows `runner isolation: dedicated account ghrunner` and
`runner admission: ...`.

## Mac CI runs

Mac CI runs `check (macOS 3.13)` on GitHub-hosted `macos-15-intel` with a
120-minute job limit. Pull requests with the `mac-ci` label run the Mac CI
coverage selected for their changes. The PM
triggers `workflow_dispatch` after each merge train to run the full main suite.
The Mac CI workflow has no scheduled trigger.

## A second runner (`davidproject-self-action-2`, trial)

    sudo bash tools/mac-runner/install.sh --account ghrunner2 \
      --runner davidproject-self-action-2 --register

The same isolation with nothing shared: its own account `ghrunner2`, folder
`/Users/ghrunner2/actions-runner`, registration (a new one, from the
released runner tarball verified by SHA-256 and a one-hour token from the
Owner's `gh`), LaunchDaemon and pf anchor `com.apple/ghrunner2`. Both
runners carry `local-mac-ci`, so GitHub gives each a job; `task_completion`
trusts exactly these two runner names. If a registration fails after GitHub
accepted the name, remove the offline runner in GitHub before re-running. The Mac has 8 GB of memory: keep the
second runner only if two jobs at once finish sooner without timeouts. To
stop it: `sudo bash tools/mac-runner/install.sh --account ghrunner2
--runner davidproject-self-action-2 --uninstall`, then remove the offline
runner under GitHub → Settings → Actions → Runners.

## Residual risk

- Every account can list other processes' command lines (`/bin/ps` is
  setuid). Environments are not shown, but Agents must never pass tokens as
  command-line arguments.
- Jobs can write `/tmp` and `/Users/Shared`, and change files ghrunner owns
  that outlive a job: its home, `.runner` and `.credentials`. One job can
  therefore affect later jobs; it cannot affect the Owner's account.
- Public internet is open (GitHub, PyPI). With a full-tunnel VPN up, that
  traffic goes through the VPN to public addresses; company hosts on
  public addresses are not blocked.
- Loopback (127.0.0.1) is open, so jobs reach other programs' local servers
  (e.g. Agent servers); they must authenticate callers.
- World-readable files outside the Owner's home (e.g. `/usr/local`,
  `/Users/Shared`, `/tmp` entries made with open permissions) are readable.
- Jobs can read the runner's own registration credential.

## Kill switch and rollback

- Stop hosted Mac CI: set the repository variable `HOSTED_CI_ENABLED` to any
  value other than `true`; the job is skipped (NOT_RUN, never PASS).
  `MAC_CI_ENABLED` is the old self-hosted switch and stays `false`.
- Stop the runner and its pf rules: `sudo bash tools/mac-runner/install.sh --uninstall`
  (pf stays on with macOS's default rules until reboot; the account is kept).
- Updating the runner: the root-owned copy cannot update itself. Update the
  runner in `~/git/actions-runner` (it must not be running), then re-run
  `install.sh`; ghrunner's work, temp and log folders are kept.
- Never re-enable the disabled GitHub-hosted workflows (`llmtool CI`,
  `nlf CI`, `ai-loop helper CI`), and never run the runner as the Owner.
  The self-hosted ai-loop job in llmtool Linux CI is a separate job.
