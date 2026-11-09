# EC2 Linux runners

The EC2 self-hosted runners are retired from CI (Owner #1253, 2026-11-09):
Linux CI runs on GitHub-hosted `ubuntu-24.04`, re-enabling needs a new Owner
grant, the machines are kept, and `llmtool task remote-test` is separate and
not CI. The page is kept as a record.

Four persistent GitHub Actions runners on the Owner's EC2 (CentOS Stream 9,
x86_64) run [llmtool Linux CI](../../.github/workflows/llmtool-linux-ci.yml)
(Issue #99). Runners 1-3 share the first host (4 vCPU / 8 GiB). Runner 4
(#1093) is on a second, smaller host (2 vCPU / 3 GiB). They are set up on the
host, not by a workflow, so a PR cannot turn the checks off.

| Runner | Account | Folder | Label |
|---|---|---|---|
| `ec2-self-runner` (id 24) | `ghrunner` | `/home/ghrunner/actions-runner` | `local-ec2-linux-ci` |
| `ec2-self-runner-2` (id 25) | `ghrunner2` | `/home/ghrunner2/actions-runner` | `local-ec2-linux-ci` |
| `ec2-self-runner-3` | `ghrunner3` | `/home/ghrunner3/actions-runner` | `local-ec2-linux-ci` |
| `ec2-self-runner-4` | `ghrunner4` | `/home/ghrunner4/actions-runner` | `local-ec2-linux-ci` |

Any runner takes any job: Python 3.14 on every PR and main push, and
Python 3.9 when the workflow is started by hand on main with `python39`
before a release (Owner 2026-10-25). The three shards can run at the same time
on any three of the four runners.

Each account is non-admin, in its own group only, with a `700` home; accounts
cannot read another runner's folder or credentials. Python 3.14 is the distro package
`python3.14` (AppStream) beside the system 3.9; jobs build their own venv.

## Fresh host

- Install `git`, `tar`, `make`, `python3.14` and the runner's libraries first
  (a missing `make` failed the first job on runner 4).
- Before `install.sh`, move `bin` and `externals` to `bin.<version>` and
  `externals.<version>` with links, or the installer refuses the node symlinks.
- With SELinux enforcing, give `runsvc.sh` type `bin_t` with `semanage fcontext`
  and `restorecon`.

## Root-owned runner install

Run the permission installer on the EC2 host after a runner has been configured.
Stop its service first. The installer also refuses to run while the service is
active or any process remains under its account. It operates on one runner
directory at a time and does not change the systemd units or their existing
drop-ins. Before installing, set `disableUpdate` to `true` in that runner's
`.runner` file (the runner configuration flag is also named `--disableupdate`):

```sh
sudo systemctl stop actions.runner.davidcatcat-davidcattoolsproject.ec2-self-runner.service
sudo bash tools/ec2-runner/install.sh --account ghrunner
sudo bash tools/ec2-runner/selftest.sh --account ghrunner
sudo systemctl start actions.runner.davidcatcat-davidcattoolsproject.ec2-self-runner.service
```

For runner 2, 3 or 4, substitute the matching account and service name in all
commands. Review the read-only plan first with
`sudo bash tools/ec2-runner/install.sh --account ghrunner2 --dry-run` (or
`ghrunner3`, `ghrunner4`). The plan does not change files.
The install root and its ordinary files/directories
become `root:root`; executable files are `0755`, other files `0644`, and
directories `0755`. `_work` and `_diag` must already exist. `_tmp` may be
absent; the installer creates it as a directory owned by the runner account
with mode `0700`. An existing `_tmp` must be a real directory, not a symlink
or another file type. `_work` and `_tmp` stay owned by the runner account with
`0700` directories. `_diag` stays account-owned with `0750` directories.
Contents under those writable directories remain runner-managed. The runner's
`bin` and `externals` links may point to their matching version directories,
and links under `externals.<version>/node*/` may point within that same
version directory. `_work/_update` is excluded from scanning and ownership
changes; the runner recreates it only when updating, so keep `disableUpdate`
enabled before installation. Other symlinks and special files are refused.
`.runner` and credential files are account-owned
`0600`; `.env` and `.path` are root-owned `0644`. The installer refuses
other symlinks, special files, and any account/path outside the fixed
`/home/ghrunnerN/actions-runner` layout.

The runner account still owns `/home/ghrunnerN` and can rename or replace its
`actions-runner` directory. This known limitation remains; ephemeral/JIT
runners are the actual fix. Configure every runner with `--disableupdate`,
because its install files are root-owned.

To roll back one runner, stop that runner's service, restore its account
ownership, then start it again. For runner 1:

```sh
sudo systemctl stop actions.runner.davidcatcat-davidcattoolsproject.ec2-self-runner.service
sudo chown -R ghrunner:ghrunner /home/ghrunner/actions-runner
sudo chmod -R u+rwX,go-rwx /home/ghrunner/actions-runner
sudo systemctl start actions.runner.davidcatcat-davidcattoolsproject.ec2-self-runner.service
```

For runners 2, 3 and 4, substitute `ghrunner2`/`ghrunner3`/`ghrunner4`, the
matching `/home/ghrunnerN/actions-runner` path, and the matching service name
ending in `ec2-self-runner-2.service`, `ec2-self-runner-3.service` or
`ec2-self-runner-4.service`. This restores
account-only ownership and access. The existing systemd drop-ins remain
installed. The recursive commands do not follow the runner's version
symlinks; inspect those links after rollback.

## Host files

Each service `actions.runner.davidcatcat-davidcattoolsproject.<runner>.service`
has drop-ins in `/etc/systemd/system/<service>.d/` that together set:

```ini
[Service]
IPAddressDeny=169.254.0.0/16       # IMDS: no AWS metadata or credentials for jobs
IPAddressDeny=fd00:ec2::254/128
Restart=always
RestartSec=5s
Environment=ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/llmtool-ci/job-started.sh
KillMode=control-group             # stop or a crash ends every process of the service
```

The three drop-ins provide IMDS denial and restart settings, the admission
hook, and `KillMode=control-group`. With the generated unit's `KillMode=process`, a
killed service left an orphan listener holding the runner's GitHub session.

`/opt/llmtool-ci/` (root-owned, `0755`) holds [job-started.sh](job-started.sh),
which the runner requires to end in `.sh`, and [job-started.py](job-started.py),
the checks. Every job must be `llmtool-linux-ci.yml`
from `davidcatcat` or `ddavidccatssun` on this repository, on the runner's own
account, pushed or started by hand on `main`,
or a PR by either account into `main`. Anything else fails before checkout.
`python3 -B -m unittest discover -s tools/ec2-runner` tests it offline.

To update the hook, copy both files to `/opt/llmtool-ci/` as root, keeping
`root:root 0755`; no restart is needed. After a drop-in change, run
`systemctl daemon-reload` and restart the idle runner.

## Rollback

- Stop Linux CI: set the repository variable `LINUX_CI_ENABLED` to anything
  but `true` (jobs are skipped, NOT_RUN). The Mac CI is separate.
- Stop one runner: `systemctl disable --now <service>`; its jobs queue.
- Remove a runner for good: stop it, then `./config.sh remove` as its account
  with a removal token, and delete the account.
