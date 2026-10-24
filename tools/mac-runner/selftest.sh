#!/bin/bash
# Local check of probe.sh and job-started.sh against a throwaway runner
# folder, run as the Owner (no sudo; it does not touch the installed runner).
# The isolation itself (account, home, pf) is checked by install.sh as
# ghrunner, and by probe.sh in every job.
set -u
src=$(cd "$(dirname "$0")" && pwd -P)
root=$(mktemp -d "${TMPDIR:-/tmp}/mac-runner-selftest.XXXXXX") && root=$(cd "$root" && pwd -P)
mkdir -p "$root/isolation" "$root/_work"
cp "$src/probe.sh" "$src/job-started.sh" "$root/isolation/"
home=$(cd ~ && pwd -P)
failures=0
check() {  # check <label> <expected exit: 0|1> <command...>
  local label=$1 want=$2; shift 2
  "$@" > "$root/out" 2>&1; local got=$?
  if { test "$want" = 0 && test $got = 0; } || { test "$want" = 1 && test $got != 0; }; then
    echo "PASS $label"
  else
    echo "FAIL $label (exit $got)"; sed 's/^/    /' "$root/out"; failures=$((failures + 1))
  fi
}
check "probe refuses a runner running as the Owner" 1 env OWNER_HOME="$home" "$root/isolation/probe.sh"
check "probe refuses without OWNER_HOME" 1 env -u OWNER_HOME "$root/isolation/probe.sh"

# Admission rules, with the probe stubbed to pass (the copy only).
printf '#!/bin/bash\nexit 0\n' > "$root/isolation/probe.sh"
event() { printf '%s' "$1" > "$root/_work/event.json"; }
hook() {  # hook <event> <actor> [VAR=value...]
  local name=$1 actor=$2; shift 2
  env GITHUB_EVENT_PATH="$root/_work/event.json" GITHUB_EVENT_NAME="$name" \
    GITHUB_REPOSITORY=davidcatcat/davidcattoolsproject GITHUB_REF=refs/heads/main \
    GITHUB_WORKFLOW_REF=davidcatcat/davidcattoolsproject/.github/workflows/llmtool-mac-ci.yml@refs/pull/1/merge \
    GITHUB_ACTOR="$actor" GITHUB_TRIGGERING_ACTOR="$actor" "$@" "$root/isolation/job-started.sh"
}
event '{"pull_request":{"head":{"repo":{"full_name":"davidcatcat/davidcattoolsproject"}},"user":{"login":"davidcatcat"}}}'
check "same-repo PR by the Owner admitted" 0 hook pull_request davidcatcat
check "PR by another actor refused" 1 hook pull_request someone-else
check "empty actor refused" 1 hook pull_request ""
check "other triggering actor refused" 1 hook pull_request davidcatcat GITHUB_TRIGGERING_ACTOR=x
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
printf '#!/bin/bash\nexit 1\n' > "$root/isolation/probe.sh"
check "failing probe refuses every job" 1 hook push davidcatcat
echo "selftest: $failures failure(s); scratch runner in $root"
test $failures = 0
