#!/bin/bash
# Fails unless the runner runs as its own non-admin account, isolated from the
# Owner's: the Owner's home is closed to it and the runner folder (with this
# probe and the hook) is not writable. The private-network block needs root
# to inspect; install.sh checks it. job-started.sh runs it before every job, so a runner started as the Owner
# (e.g. ./run.sh by hand) fails every job.
set -u
fail() { echo "::error::runner isolation check failed: $1"; exit 1; }
: "${OWNER_HOME:?OWNER_HOME is not set; run tools/mac-runner/install.sh}"
root=$(cd "$(dirname "$0")/.." && pwd -P) || fail "cannot find the runner root"

denied() { ! "$@" >/dev/null 2>&1; }
test -d "$OWNER_HOME" || fail "OWNER_HOME is not a folder"
test "$(id -u)" != "$(stat -f %u "$OWNER_HOME")" || fail "the runner runs as the Owner"
id -Gn | tr ' ' '\n' | grep -qx admin && fail "the runner account is an admin"
denied ls "$OWNER_HOME" || fail "the Owner's home is readable"
denied touch "$root/.isolation-probe" || fail "the runner folder is writable"
denied touch "$root/isolation/job-started.sh" || fail "the hook is writable"
test -w "$root/_work" || fail "the runner work folder is not writable"
echo "runner isolation: dedicated account $(id -un) (Owner's home closed, runner folder read-only)"
