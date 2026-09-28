#!/usr/bin/env bash
# Runtime smoke test for a built nlf binary.
#
# Usage: bash scripts/ci-smoke.sh <binary> <version>
#
# Builds a small fixture tree in a fresh temporary directory, runs the binary
# against it, and checks exit codes, exact output path sets, --explain,
# --confirm, and that nothing in the fixture was modified. Portable across
# GNU/Linux and macOS (bash 3.2+). Temporary directories are left in place
# and printed at the end; nothing is deleted.
set -euo pipefail

usage() {
	printf 'usage: %s <binary> <version>\n' "$0" >&2
	exit 1
}

[ "$#" -eq 2 ] || usage
bin_arg=$1
version=$2

if [ -z "$version" ]; then
	printf 'ci-smoke: version is empty\n' >&2
	exit 1
fi
if [ ! -f "$bin_arg" ]; then
	printf 'ci-smoke: binary not found: %s\n' "$bin_arg" >&2
	exit 1
fi
if [ ! -x "$bin_arg" ]; then
	printf 'ci-smoke: binary is not executable: %s\n' "$bin_arg" >&2
	exit 1
fi
bin_dir=$(cd "$(dirname "$bin_arg")" && pwd -P)
bin="$bin_dir/$(basename "$bin_arg")"

case "$(uname -s)" in
Darwin) platform=macOS ;;
Linux) platform=GNU/Linux ;;
*) platform="unsupported OS $(uname -s)" ;;
esac

# --- fixture -----------------------------------------------------------------

base=$(mktemp -d "${TMPDIR:-/tmp}/nlf-smoke.XXXXXX")
fx=$(cd "$base" && pwd -P)
work=$(mktemp -d "${TMPDIR:-/tmp}/nlf-smoke-work.XXXXXX")
work=$(cd "$work" && pwd -P)
errf="$work/stderr"
inf="$work/stdin"

mkdir -p "$fx/vendor" "$fx/docs" "$fx/中文 目錄"
printf 'package main\n\n// TODO: finish\nfunc main() {}\n' >"$fx/main.go"
printf 'package main\n\nfunc util() {}\n' >"$fx/util.go"
printf 'package lib\n\n// TODO: vendored\n' >"$fx/vendor/lib.go"
printf '# readme\n' >"$fx/docs/readme.md"
printf '說明\n' >"$fx/中文 目錄/說明.md"
printf 'x%.0s' $(seq 1 3000) >"$fx/big.log"
printf 'package old\n' >"$fx/old.go"
touch -t 202001011200 "$fx/old.go"

snapshot() {
	(cd "$fx" && find . -type f -exec cksum {} + | LC_ALL=C sort)
}
before=$(snapshot)
sleep 1
marker="$work/marker"
printf 'marker\n' >"$marker"

# Runs from inside the fixture so a query without --dir cannot reach anything else.
cd "$fx"

# --- helpers -----------------------------------------------------------------

nfail=0
failures=""

pass() {
	printf 'PASS  %s\n' "$1"
}

fail() {
	printf 'FAIL  %s: %s\n' "$1" "$2"
	nfail=$((nfail + 1))
	failures="${failures}  - $1: $2
"
}

# run <stdin> <args...>: sets code, out, err.
run() {
	printf '%s' "$1" >"$inf"
	shift
	code=0
	out=$("$bin" "$@" <"$inf" 2>"$errf") || code=$?
	err=$(cat "$errf")
}

sorted() {
	printf '%s\n' "$1" | LC_ALL=C sort
}

contains() {
	case "$1" in
	*"$2"*) return 0 ;;
	*) return 1 ;;
	esac
}

# check_found <name> <expected newline-separated paths> <args...>
check_found() {
	local name=$1 want=$2
	shift 2
	run "" "$@"
	if [ "$code" -ne 0 ]; then
		fail "$name" "exit $code, want 0 (stderr: $err)"
	elif [ "$(sorted "$out")" != "$(sorted "$want")" ]; then
		fail "$name" "got [$(sorted "$out" | tr '\n' ' ')] want [$(sorted "$want" | tr '\n' ' ')]"
	else
		pass "$name"
	fi
}

# check_exit_empty <name> <want code> <args...>
check_exit_empty() {
	local name=$1 want=$2
	shift 2
	run "" "$@"
	if [ "$code" -ne "$want" ]; then
		fail "$name" "exit $code, want $want (stderr: $err)"
	elif [ -n "$out" ]; then
		fail "$name" "stdout not empty: $out"
	else
		pass "$name"
	fi
}

# --- assertions --------------------------------------------------------------

run "" --version
if [ "$code" -eq 0 ] && [ "$out" = "nlf $version" ]; then
	pass "--version"
else
	fail "--version" "exit $code, stdout [$out], want [nlf $version]"
fi

run "" --help
if [ "$code" -eq 0 ] && contains "$out" "Usage:"; then
	pass "--help"
else
	fail "--help" "exit $code, stdout lacks Usage:"
fi

nl='
'
check_found "go files excluding vendor" \
	"$fx/main.go$nl$fx/util.go$nl$fx/old.go" \
	--dir "$fx" "go files excluding vendor"
check_found "找 go 檔" \
	"$fx/main.go$nl$fx/util.go$nl$fx/old.go$nl$fx/vendor/lib.go" \
	--dir "$fx" "找 go 檔"
check_found 'files containing "TODO"' \
	"$fx/main.go$nl$fx/vendor/lib.go" \
	--dir "$fx" 'files containing "TODO"'
check_found "larger than 2KB" \
	"$fx/big.log" \
	--dir "$fx" "larger than 2KB"
check_found "go files modified in the last 7 days" \
	"$fx/main.go$nl$fx/util.go$nl$fx/vendor/lib.go" \
	--dir "$fx" "go files modified in the last 7 days"
check_found "UTF-8 --dir with a space: md files" \
	"$fx/中文 目錄/說明.md" \
	--dir "$fx/中文 目錄" "md files"

check_exit_empty "pem files (no match)" 1 --dir "$fx" "pem files"

check_exit_empty "files without TODO (invalid)" 2 --dir "$fx" "files without TODO"
check_exit_empty "--bogus go files (invalid flag)" 2 --bogus go files
check_exit_empty "--dir missing (invalid)" 2 --dir "$fx/does-not-exist" "go files"
check_exit_empty 'log files !containing "TODO" (invalid)' 2 --dir "$fx" 'log files !containing "TODO"'

run "" --explain --dir "$fx" "go files"
if [ "$code" -ne 0 ]; then
	fail "--explain" "exit $code, want 0 (stderr: $err)"
elif ! contains "$out" "find "; then
	fail "--explain" "stdout lacks 'find '"
elif ! contains "$out" "$platform"; then
	fail "--explain" "stdout lacks platform name '$platform'"
else
	pass "--explain ($platform)"
fi

run "" --dir "$fx" "go files"
direct=$out

run "n$nl" --confirm --dir "$fx" "go files"
if [ "$code" -eq 0 ] && [ -z "$out" ] && contains "$err" "Cancelled."; then
	pass "--confirm answered n"
else
	fail "--confirm answered n" "exit $code, stdout [$out], stderr lacks Cancelled.?"
fi

run "y$nl" --confirm --dir "$fx" "go files"
if [ "$code" -ne 0 ]; then
	fail "--confirm answered y" "exit $code, want 0 (stderr: $err)"
elif [ -z "$out" ] || [ "$out" != "$direct" ]; then
	fail "--confirm answered y" "stdout differs from the direct run"
elif ! contains "$err" "READ-ONLY"; then
	fail "--confirm answered y" "stderr lacks READ-ONLY"
else
	pass "--confirm answered y"
fi

run "" --confirm --dir "$fx" "go files"
if [ "$code" -eq 0 ] && [ -z "$out" ]; then
	pass "--confirm with EOF"
else
	fail "--confirm with EOF" "exit $code, stdout [$out]"
fi

after=$(snapshot)
if [ "$before" = "$after" ]; then
	pass "fixture checksums unchanged"
else
	fail "fixture checksums unchanged" "checksum snapshot differs"
fi

newer=$(find "$fx" -newer "$marker")
if [ -z "$newer" ]; then
	pass "nothing in fixture modified after marker"
else
	fail "nothing in fixture modified after marker" "$(printf '%s' "$newer" | tr '\n' ' ')"
fi

# --- summary -----------------------------------------------------------------

printf 'fixture: %s\n' "$fx"
printf 'work:    %s\n' "$work"
if [ "$nfail" -ne 0 ]; then
	printf '\n%d check(s) failed for %s:\n%s' "$nfail" "$bin" "$failures"
	exit 1
fi
printf 'all checks passed for %s\n' "$bin"
