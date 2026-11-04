#!/bin/bash
# The runner's job-started hook (ACTIONS_RUNNER_HOOK_JOB_STARTED, set by
# install.sh). It lives in the runner directory, which jobs cannot write, so a
# pull request cannot change it. A non-zero exit fails the job before any
# workflow step runs.
set -u
fail() { echo "::error::runner admission refused: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd -P) || fail "cannot find the hook directory"
"$here/probe.sh" || exit 1

# Only this repository, only workflows written for this runner, and only code
# pushed by a trusted account. Forks and other events never run here.
repo=davidcatcat/davidcattoolsproject
trusted_actors=" davidcatcat ddavidccatssun "
workflows=" llmtool-mac-ci.yml mac-runner-smoke.yml "

event() { plutil -extract "$1" raw -o - "$GITHUB_EVENT_PATH" 2>/dev/null; }
test "${GITHUB_REPOSITORY:-}" = "$repo" || fail "repository ${GITHUB_REPOSITORY:-unset}"
case "${GITHUB_WORKFLOW_REF:-}" in
  "$repo/.github/workflows/"*@*) ;;
  *) fail "workflow ${GITHUB_WORKFLOW_REF:-unset}" ;;
esac
file=${GITHUB_WORKFLOW_REF#"$repo/.github/workflows/"}
file=${file%%@*}
case "$workflows" in *" $file "*) ;; *) fail "workflow $file is not admitted" ;; esac
for actor in "${GITHUB_ACTOR:-}" "${GITHUB_TRIGGERING_ACTOR:-}"; do
  case "$trusted_actors" in *" $actor "*) ;; *) fail "actor ${actor:-unset}" ;; esac
done
case "${GITHUB_EVENT_NAME:-}" in
  push)
    test "${GITHUB_REF:-}" = refs/heads/main || fail "push to ${GITHUB_REF:-unset}" ;;
  pull_request)
    test "$(event pull_request.head.repo.full_name)" = "$repo" || fail "pull request from a fork"
    author=$(event pull_request.user.login)
    case "$trusted_actors" in *" $author "*) ;; *) fail "pull request author ${author:-unset}" ;; esac ;;
  workflow_dispatch) ;;
  *) fail "event ${GITHUB_EVENT_NAME:-unset}" ;;
esac
echo "runner admission: $GITHUB_EVENT_NAME $file by $GITHUB_ACTOR"
