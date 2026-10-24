#!/bin/bash
# Installs the runner isolation (runner.sb via profile.sh, probe.sh,
# job-started.sh) into a
# configured Actions runner and restarts its launchd service under the
# sandbox. Run it as the runner's account, outside any sandbox, from a main
# checkout: the installed copies are what every job is checked against.
# No sudo. Re-running it replaces the installed copies. To take the runner
# out of service: ./svc.sh stop in the runner directory (see README.md).
set -euo pipefail
fail() { echo "install: $1" >&2; exit 1; }
src=$(cd "$(dirname "$0")" && pwd -P)
root=$(cd "${1:-$HOME/git/actions-runner}" && pwd -P) || fail "no runner directory"
test -f "$root/.runner" || fail "$root is not a configured runner"
test -f "$root/bin/runsvc.sh" || fail "$root/bin/runsvc.sh is missing"
plist=$(cat "$root/.service") || fail "the runner service is not installed (./svc.sh install)"
test -f "$plist" || fail "missing $plist"
home=$(cd ~ && pwd -P)
case "$root" in "$home"/*/*) ;; *) fail "the runner must be in a folder below $home" ;; esac

cd "$root"
./svc.sh stop >/dev/null 2>&1 || true

install -d -m 0700 _home _tmp
install -d -m 0755 isolation
"$src/profile.sh" "$root" > isolation/runner.sb.new
chmod 0444 isolation/runner.sb.new
mv -f isolation/runner.sb.new isolation/runner.sb
install -m 0555 "$src/probe.sh" "$src/job-started.sh" isolation/

lang=$(sed -n 's/^LANG=//p' .env 2>/dev/null | head -n 1)
cat > .env <<EOF
LANG=${lang:-en_US.UTF-8}
HOME=$root/_home
TMPDIR=$root/_tmp/
REAL_HOME=$home
ACTIONS_RUNNER_HOOK_JOB_STARTED=$root/isolation/job-started.sh
EOF
echo /usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin > .path

# The service script is the runner's own template with the listener started
# under the sandbox; every job is a child of the listener.
node='./externals/$nodever/bin/node ./bin/RunnerService.js &'
grep -qF "$node" bin/runsvc.sh || fail "unexpected bin/runsvc.sh; review the runner upgrade"
sandboxed="/usr/bin/sandbox-exec -D REAL_HOME=$home -D RUNNER_ROOT=$root -f $root/isolation/runner.sb $node"
awk -v old="$node" -v new="$sandboxed" '$0 == old { print new; next } { print }' \
  bin/runsvc.sh > runsvc.sh.new
grep -qF "$sandboxed" runsvc.sh.new || fail "could not patch runsvc.sh"
chmod 0755 runsvc.sh.new
mv runsvc.sh.new runsvc.sh

./svc.sh start >/dev/null
echo "installed isolation in $root; service restarted under the sandbox"
echo "check: run the Mac Runner Smoke Test workflow and look for 'runner isolation: sandboxed'"
