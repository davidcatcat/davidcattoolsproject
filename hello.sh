#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || -z "$1" ]]; then
  echo "Usage: $(basename "$0") <name>" >&2
  echo "Example: $(basename "$0") David" >&2
  exit 1
fi

echo "Hello, $1!"
