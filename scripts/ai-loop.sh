#!/usr/bin/env bash
# Download this launcher once; every invocation fetches the current task and runner.
set -euo pipefail
umask 077
repo=/Users/davidsun/git/davidcataiporject/davidcattoolsproject
control=automation/ai-loop
cd "$repo"
test "$(pwd -P)" = "$repo" || { echo 'STOP: wrong canonical repository'; exit 1; }
test "$(git rev-parse --show-toplevel)" = "$repo" || { echo 'STOP: wrong Git root'; exit 1; }
case "$(git remote get-url origin)" in
  git@github.com:davidcatcat/davidcattoolsproject.git|https://github.com/davidcatcat/davidcattoolsproject.git|https://github.com/davidcatcat/davidcattoolsproject|ssh://git@github.com/davidcatcat/davidcattoolsproject.git) ;;
  *) echo 'STOP: origin is not davidcatcat/davidcattoolsproject'; exit 1 ;;
esac
command -v python3 >/dev/null || { echo 'STOP: python3 is required'; exit 1; }
echo '[Download] Fetching runner and task from Git...'
git fetch --no-tags origin "refs/heads/$control:refs/remotes/origin/$control"
revision=$(git rev-parse "refs/remotes/origin/$control")
cache=$(git rev-parse --absolute-git-dir)/ai-loop/bootstrap/$revision
mkdir -p "$cache"
git show "$revision:scripts/ai_loop.py" > "$cache/ai_loop.py"
# Stable entry point after the first launch; never copies/creates a project checkout.
git show "$revision:scripts/ai-loop.sh" > "$cache/ai-loop.sh"
ln -sfn "$cache/ai-loop.sh" "$(git rev-parse --absolute-git-dir)/ai-loop/run.sh"
exec python3 "$cache/ai_loop.py" --control-sha "$revision" "$@"
