#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")"
pass=0
fail=0

check() {
  local desc="$1" cond="$2"
  if [[ "$cond" == "0" ]]; then
    echo "PASS: $desc"; pass=$((pass + 1))
  else
    echo "FAIL: $desc"; fail=$((fail + 1))
  fi
}

# Test 1: normal usage
out="$(./hello.sh David)"; rc=$?
[[ $rc -eq 0 && "$out" == "Hello, David!" ]]; check "normal usage prints 'Hello, David!' and exits 0" $?

# Test 2: missing argument
err="$(./hello.sh 2>&1 >/dev/null)"; rc=$?
[[ $rc -ne 0 ]]; check "missing argument exits non-zero (rc=$rc)" $?
[[ "$err" == *Usage:* ]]; check "missing argument prints usage to stderr" $?

echo "----"
echo "Passed: $pass  Failed: $fail"
[[ $fail -eq 0 ]]
