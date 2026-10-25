# EC2 Linux runners

Two persistent GitHub Actions runners on the Owner's EC2 (CentOS Stream 9,
x86_64, 4 vCPU / 8 GiB) run [llmtool Linux CI](../../.github/workflows/llmtool-linux-ci.yml)
(Issue #99). They are set up on the host, not by a workflow, so a PR cannot
turn the checks off.

| Runner | Account | Folder | Label |
|---|---|---|---|
| `ec2-self-runner` (id 24) | `ghrunner` | `/home/ghrunner/actions-runner` | `local-ec2-linux-ci` |
| `ec2-self-runner-2` (id 25) | `ghrunner2` | `/home/ghrunner2/actions-runner` | `local-ec2-linux-ci` |

Either runner takes any job: Python 3.14 on every PR and main push, and
Python 3.9 when the workflow is started by hand on main with `python39`
before a release (Owner 2026-10-25). Two jobs (two PRs, or 3.14 and 3.9)
run at the same time; on 10-25 the pair took 7.5 minutes against 14.75 in
a row on one runner.

Each account is non-admin, in its own group only, with a `700` home; neither
can read the other's folder or credential. Python 3.14 is the distro package
`python3.14` (AppStream) beside the system 3.9; jobs build their own venv.

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

The files are `10-imds-deny.conf` + `15-restart.conf` (runner 1) or
`10-security-restart.conf` (runner 2), then `20-admission.conf` and
`30-killmode.conf` on both. With the generated unit's `KillMode=process`, a
killed service left an orphan listener holding the runner's GitHub session.

`/opt/llmtool-ci/` (root-owned, `0755`) holds [job-started.sh](job-started.sh),
which the runner requires to end in `.sh`, and [job-started.py](job-started.py),
the checks. Every job must be `llmtool-linux-ci.yml` from the Owner on this
repository, on the runner's own account, pushed or started by hand on `main`,
or an Owner PR into `main`. Anything else fails before checkout.
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
