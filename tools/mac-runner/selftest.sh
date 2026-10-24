#!/bin/bash
# Local check of runner.sb, probe.sh and job-started.sh against a throwaway
# runner directory. Run it outside any sandbox (a CI job cannot nest one).
# It does not touch the installed runner.
set -u
src=$(cd "$(dirname "$0")" && pwd -P)
root=$(mktemp -d "${TMPDIR:-/tmp}/mac-runner-selftest.XXXXXX") && root=$(cd "$root" && pwd -P)
mkdir -p "$root/isolation" "$root/_work" "$root/_diag" "$root/_home" "$root/_tmp"
cp "$src/probe.sh" "$src/job-started.sh" "$src/bin/ps" "$root/isolation/"
"$src/profile.sh" "$root" > "$root/isolation/runner.sb"
home=$(cd ~ && pwd -P)
failures=0
check() {  # check <label> <expected exit: 0|1> <command...>
  local label=$1 want=$2; shift 2
  "$@" > "$root/_tmp/out" 2>&1; local got=$?
  if { test "$want" = 0 && test $got = 0; } || { test "$want" = 1 && test $got != 0; }; then
    echo "PASS $label"
  else
    echo "FAIL $label (exit $got)"; sed 's/^/    /' "$root/_tmp/out"; failures=$((failures + 1))
  fi
}
sbx() {
  REAL_HOME=$home /usr/bin/sandbox-exec -D REAL_HOME="$home" -D RUNNER_ROOT="$root" -f "$root/isolation/runner.sb" "$@"
}
event() { printf '%s' "$1" > "$root/_work/event.json"; }
hook() {  # hook <event> <actor> [VAR=value...]
  local name=$1 actor=$2; shift 2
  sbx /usr/bin/env GITHUB_EVENT_PATH="$root/_work/event.json" GITHUB_EVENT_NAME="$name" \
    GITHUB_REPOSITORY=davidcatcat/davidcattoolsproject GITHUB_REF=refs/heads/main \
    GITHUB_WORKFLOW_REF=davidcatcat/davidcattoolsproject/.github/workflows/llmtool-mac-ci.yml@refs/pull/1/merge \
    GITHUB_ACTOR="$actor" GITHUB_TRIGGERING_ACTOR="$actor" "$@" "$root/isolation/job-started.sh"
}
same='{"pull_request":{"head":{"repo":{"full_name":"davidcatcat/davidcattoolsproject"}},"user":{"login":"davidcatcat"}}}'

check "probe fails outside the sandbox" 1 env REAL_HOME="$home" "$root/isolation/probe.sh"
check "probe passes inside the sandbox" 0 sbx /usr/bin/env REAL_HOME="$home" "$root/isolation/probe.sh"
check "home unreadable" 1 sbx /bin/ls "$home/Library"
check "keychain item unreadable" 1 sbx /usr/bin/security find-generic-password -s gh:github.com
check "launchctl blocked" 1 sbx /bin/launchctl list
check "open blocked" 1 sbx /usr/bin/open -g -a TextEdit
check "osascript blocked" 1 sbx /usr/bin/osascript -e 'return 1'
# A process of the account outside the sandbox and outside this script's
# own (possibly sandboxed) shell: the login window of the GUI session.
other=$(pgrep -x -u "$(id -u)" loginwindow | head -n 1)
check "found the login window process" 0 test -n "$other"
check "signal to another process of the account blocked" 1 sbx /bin/kill -0 "$other"
check "ps of another process blocked" 1 sbx /bin/ps -p "$other"
args='import ctypes,sys; l=ctypes.CDLL(None); m=(ctypes.c_int*3)(1,49,int(sys.argv[1])); n=ctypes.c_size_t(65536); b=ctypes.create_string_buffer(65536); sys.exit(l.sysctl(m,3,b,ctypes.byref(n),None,0) != 0)'
check "another process's arguments/environment unreadable" 1 sbx /usr/bin/python3 -I -c "$args" "$other"
check "own arguments/environment readable" 0 sbx /bin/bash -c '/usr/bin/python3 -I -c "$1" $$' - "$args"
check "ps substitute sees own child" 0 sbx /bin/bash -c \
  'sleep 5 & /usr/bin/python3 -I "$1" -o pid=,ppid=,pgid=,stat= -p $! | grep -q "^$! $$ "' - "$root/isolation/ps"
check "signal to own child in a new session allowed" 0 sbx /usr/bin/perl -MPOSIX -e \
  'my $p = fork // die; if (!$p) { POSIX::setsid(); sleep 30; exit } sleep 1; kill("TERM", $p) or die; waitpid($p, 0)'
check "own folder in /private/tmp" 0 sbx /usr/bin/mktemp -d /private/tmp/tmpselftest.XXXXXX
check "other name in /private/tmp refused" 1 sbx /usr/bin/mktemp -d /private/tmp/selftest.XXXXXX
check "Agent folder in /private/tmp unreadable" 1 sbx /bin/ls "/private/tmp/claude-$(id -u)"
check "write to /usr/local blocked" 1 sbx /usr/bin/touch /usr/local/.selftest
check "write to the runner bin blocked" 1 sbx /usr/bin/touch "$root/isolation/x"
check "work folder writable" 0 sbx /usr/bin/touch "$root/_work/x"
check "terminals of the account closed" 1 sbx /usr/bin/python3 -I -c 'import pty; pty.openpty()'
check "HTTPS to GitHub works" 0 sbx /usr/bin/curl -fsS -o /dev/null https://api.github.com
event "$same"
check "same-repo PR by the Owner admitted" 0 hook pull_request davidcatcat
check "PR by another actor refused" 1 hook pull_request someone-else
check "empty actor refused" 1 hook pull_request ""
event '{"pull_request":{"head":{"repo":{"full_name":"x/fork"}},"user":{"login":"davidcatcat"}}}'
check "PR from a fork refused" 1 hook pull_request davidcatcat
event '{"pull_request":{"head":{"repo":{"full_name":"davidcatcat/davidcattoolsproject"}},"user":{"login":"other"}}}'
check "PR authored by another account refused" 1 hook pull_request davidcatcat
event '{}'
check "push to main admitted" 0 hook push davidcatcat
check "push to a branch refused" 1 hook push davidcatcat GITHUB_REF=refs/heads/feature
check "pull_request_target refused" 1 hook pull_request_target davidcatcat
check "other workflow refused" 1 hook workflow_dispatch davidcatcat \
  GITHUB_WORKFLOW_REF=davidcatcat/davidcattoolsproject/.github/workflows/nlf-ci.yml@refs/heads/main
check "other repository refused" 1 hook workflow_dispatch davidcatcat GITHUB_REPOSITORY=x/y
check "manual run of the smoke test admitted" 0 hook workflow_dispatch davidcatcat \
  GITHUB_WORKFLOW_REF=davidcatcat/davidcattoolsproject/.github/workflows/mac-runner-smoke.yml@refs/heads/main
check "unsandboxed hook refused" 1 /usr/bin/env REAL_HOME="$home" GITHUB_EVENT_NAME=push \
  GITHUB_REPOSITORY=davidcatcat/davidcattoolsproject GITHUB_REF=refs/heads/main \
  GITHUB_ACTOR=davidcatcat GITHUB_TRIGGERING_ACTOR=davidcatcat \
  GITHUB_WORKFLOW_REF=davidcatcat/davidcattoolsproject/.github/workflows/llmtool-mac-ci.yml@refs/heads/main \
  "$root/isolation/job-started.sh"
echo "selftest: $failures failure(s); scratch runner in $root"
test $failures = 0
