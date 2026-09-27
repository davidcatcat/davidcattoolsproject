#!/bin/bash
# hello.sh - print a greeting for the given name
# Usage: ./hello.sh <name>

if [ $# -lt 1 ]; then
  echo "Usage: $0 <name>" >&2
  exit 1
fi

echo "Hello, $1!"
