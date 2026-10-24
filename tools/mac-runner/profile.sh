#!/bin/bash
# Prints the sandbox profile for the runner folder $1: runner.sb plus the
# folder's ancestors as single directories. Path checks in llmtool open each
# ancestor (openat walk from /); this shows their entry names, not the
# contents of anything below them.
set -euo pipefail
root=$1
cat "$(cd "$(dirname "$0")" && pwd -P)/runner.sb"
echo '(allow file-read-data'
dir=$root
while dir=$(dirname "$dir") && test "$dir" != /; do
  printf '  (literal "%s")\n' "$dir"
done
echo ')'
