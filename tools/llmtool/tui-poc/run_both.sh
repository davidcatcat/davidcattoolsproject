#!/bin/sh
# Run the PoC scenarios against two trees in turn (sequential: the child-stats sampler uses pgrep).
# Usage: POC_OUT=<dir> W42_ROOT=<tools/llmtool of the W42 checkout> MAIN_ROOT=<tools/llmtool of main> sh run_both.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
: "${POC_PYTHON:=/Users/davidsun/git/davidcataiporject/davidcattoolsproject/tools/llmtool/.venv/bin/python}"
: "${POC_HOME:?scratch HOME with .config/llmtool/config.yaml}"
: "${POC_OUT:?output directory}"
for pair in "w42:${W42_ROOT:?}" "main:${MAIN_ROOT:?}"; do
  label=${pair%%:*}; root=${pair#*:}
  echo "== $label ($root)"
  LLMTOOL_ROOT=$root POC_LABEL=$label POC_PYTHON=$POC_PYTHON POC_HOME=$POC_HOME POC_OUT=$POC_OUT \
    /usr/bin/time -l node "$HERE/poc.mjs" 2> "$POC_OUT/$label-time.txt" || echo "$label: node exited $?"
  grep -E "real|maximum resident" "$POC_OUT/$label-time.txt"
done
