#!/usr/bin/env bash
# Transitional standalone launcher. It executes only the checked-out source.
set -euo pipefail
umask 077

repo=${AI_LOOP_REPO:-$(pwd -P)}
task_dir=${AI_LOOP_TASK_DIR:-"$repo/tools/ai-loop/tasks"}
while (($#)); do
  case "$1" in
    --repo) (($# >= 2)) || { echo 'STOP: --repo needs a path' >&2; exit 2; }; repo=$2; shift 2 ;;
    --task-dir) (($# >= 2)) || { echo 'STOP: --task-dir needs a path' >&2; exit 2; }; task_dir=$2; shift 2 ;;
    *) echo "STOP: unsupported launcher argument: $1" >&2; exit 2 ;;
  esac
done
repo=$(cd "$repo" 2>/dev/null && pwd -P) || { echo 'STOP: repository path is unavailable' >&2; exit 1; }
task_dir=$(cd "$task_dir" 2>/dev/null && pwd -P) || { echo 'STOP: task directory is unavailable' >&2; exit 1; }
git -C "$repo" rev-parse --show-toplevel >/dev/null 2>&1 || { echo 'STOP: not a Git checkout' >&2; exit 1; }
test "$(git -C "$repo" rev-parse --show-toplevel)" = "$repo" || { echo 'STOP: repo must be the Git root' >&2; exit 1; }
case "$(git -C "$repo" remote get-url origin 2>/dev/null || true)" in
  git@github.com:davidcatcat/davidcattoolsproject.git|https://github.com/davidcatcat/davidcattoolsproject.git|https://github.com/davidcatcat/davidcattoolsproject|ssh://git@github.com/davidcatcat/davidcattoolsproject.git) ;;
  *) echo 'STOP: origin is not davidcatcat/davidcattoolsproject' >&2; exit 1 ;;
esac
test "$(git -C "$repo" branch --show-current)" = main || { echo 'STOP: launcher requires the main branch' >&2; exit 1; }
test -z "$(git -C "$repo" status --porcelain --untracked-files=all)" || { echo 'STOP: working tree is not clean' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'STOP: python3 is required' >&2; exit 1; }
exec python3 -B "$(dirname "$0")/ai_loop.py" --repo "$repo" --task-dir "$task_dir"
