#!/bin/bash
# test.sh - tests for hello.sh
# Verifies:
#   1. `hello.sh David` prints "Hello, David!" and exits 0
#   2. `hello.sh` (no args) prints a usage message to stderr and exits non-zero

set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
HELLO="$DIR/hello.sh"

fail=0

# Test 1: with a name argument
output=$("$HELLO" David 2>/dev/null)
status=$?

if [ "$status" -ne 0 ]; then
  echo "FAIL: hello.sh David exited with $status, expected 0"
  fail=1
elif [ "$output" != "Hello, David!" ]; then
  echo "FAIL: hello.sh David output was '$output', expected 'Hello, David!'"
  fail=1
else
  echo "PASS: hello.sh David -> '$output' (exit $status)"
fi

# Test 2: with no arguments
error_output=$("$HELLO" 2>&1 1>/dev/null)
status=$?

if [ "$status" -eq 0 ]; then
  echo "FAIL: hello.sh with no args exited with 0, expected non-zero"
  fail=1
elif [ -z "$error_output" ]; then
  echo "FAIL: hello.sh with no args produced no usage message"
  fail=1
else
  echo "PASS: hello.sh with no args -> usage message printed, exit $status"
fi

if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
  exit 0
else
  echo "SOME TESTS FAILED"
  exit 1
fi
