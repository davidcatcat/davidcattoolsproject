#!/bin/bash
# Fails unless this process runs inside runner.sb: the account's files and
# shared temp folders are unreadable and nothing outside the runner's own
# folders is writable. job-started.sh runs it before every job, so a runner
# started without the sandbox (e.g. ./run.sh by hand) fails every job.
set -u
fail() { echo "::error::runner isolation check failed: $1"; exit 1; }
: "${REAL_HOME:?REAL_HOME is not set; run tools/mac-runner/install.sh}"
root=$(cd "$(dirname "$0")/.." && pwd -P) || fail "cannot find the runner root"

denied() { ! "$@" >/dev/null 2>&1; }
test -d "$REAL_HOME" || fail "REAL_HOME is not the account's home"
denied ls "$REAL_HOME/Library" || fail "the account's home is readable"
denied ls "/private/tmp/claude-$(id -u)" || fail "Agent temp folders are readable"
denied touch "$root/.isolation-probe" || fail "the runner directory is writable"
denied touch /usr/local/.isolation-probe || fail "/usr/local is writable"
security list-keychains 2>/dev/null | grep -q keychain && fail "the keychain is reachable"
test -w "$root/_work" || fail "the runner work folder is not writable"
echo "runner isolation: sandboxed (home, temp, keychain, writes outside the runner denied)"
